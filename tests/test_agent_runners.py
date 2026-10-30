"""Real HTTP task execution and orchestration-only labels."""

import json
import threading
from contextlib import suppress
from pathlib import Path

import pytest

from common.contracts import TrafficClass
from harness.agents.runner import RunnerConfig, Task, plan_tasks, run_tasks
from harness.mcp_target.server import create_server


def test_seeded_plan_and_boundaries() -> None:
    tasks = tuple(Task("compute", {"values": [value]}) for value in range(10))
    config = RunnerConfig(2, (0.0, 0.01), tasks, seed=4)
    assert plan_tasks(config) == plan_tasks(config)
    assert plan_tasks(config) != plan_tasks(RunnerConfig(2, (0.0, 0.01), tasks, seed=5))
    with pytest.raises(ValueError):
        RunnerConfig(0, 0.0, tasks, seed=4)
    with pytest.raises(ValueError):
        RunnerConfig(1, float("nan"), tasks, seed=4)
    with pytest.raises(ValueError):
        RunnerConfig(1, 0.0, tasks, seed=True)


def test_real_mcp_tasks_emit_capture_window_and_completion_log(tmp_path: Path) -> None:
    server = create_server("127.0.0.1", 0, tmp_path / "target.jsonl")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = RunnerConfig(
        2,
        0.0,
        (Task("search", {"query": "network"}), Task("fetch", {"document_id": "networking"})),
        seed=13,
    )
    output = tmp_path / "runner.jsonl"
    try:
        window = run_tasks(
            config,
            f"http://127.0.0.1:{server.server_port}/mcp",
            "127.0.0.1",
            TrafficClass.AGENT_INTERACTIVE,
            output,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert window.source_framework == "mcp_task_client"
    assert window.label == TrafficClass.AGENT_INTERACTIVE
    records = [json.loads(line) for line in output.read_text().splitlines()]
    assert [record["event"] for record in records] == ["run_start", "task", "task", "run_end"]
    assert all(record["success"] for record in records if record["event"] == "task")
    assert records[-1]["capture_window"]["source_ip"] == "127.0.0.1"
    with pytest.raises(FileExistsError):
        run_tasks(
            config, "http://127.0.0.1:1/mcp", "127.0.0.1", TrafficClass.AGENT_INTERACTIVE, output
        )


def test_task_failures_retain_an_unsuccessful_run_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from harness.agents.runner import RunnerError

    monkeypatch.setattr(
        "harness.agents.runner.McpClient.request",
        lambda *args: (_ for _ in ()).throw(RunnerError("unreachable")),
    )
    output = tmp_path / "failure.jsonl"
    with pytest.raises(RunnerError, match="unreachable"):
        run_tasks(
            RunnerConfig(1, 0.0, (Task("search", {"query": "x"}),), seed=1),
            "http://127.0.0.1:1/mcp",
            "127.0.0.1",
            TrafficClass.AGENT_INTERACTIVE,
            output,
        )
    records = [json.loads(line) for line in output.read_text().splitlines()]
    assert records[-1]["success"] is False
    assert records[-1]["capture_window"]["source_framework"] == "mcp_task_client"


def test_real_external_command_output_and_timeout_are_retained(tmp_path: Path) -> None:
    import sys

    from harness.agents.runner import RunnerError, run_external_framework

    output = tmp_path / "external.log"
    run_external_framework(
        (sys.executable, "-c", "import sys; sys.stdout.write('completed')"), output, timeout_s=3
    )
    assert output.read_text() == "completed"
    with pytest.raises(FileExistsError):
        run_external_framework((sys.executable, "-c", "pass"), output, timeout_s=3)
    timeout_log = tmp_path / "timeout.log"
    with pytest.raises(RunnerError, match="external framework failed"):
        run_external_framework(
            (sys.executable, "-c", "import time; time.sleep(10)"), timeout_log, timeout_s=0.1
        )
    assert timeout_log.exists()


@pytest.mark.parametrize("outcome", ["timeout", "parent_exits", "interrupted"])
def test_external_framework_stops_descendant_traffic(
    tmp_path: Path, outcome: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import signal
    import socket
    import subprocess
    import sys
    import time

    from harness.agents.runner import RunnerError, run_external_framework

    pid_file = tmp_path / "descendant.pid"
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
        receiver.bind(("127.0.0.1", 0))
        receiver.settimeout(0.2)
        child_code = (
            "import os, pathlib, signal, socket, time; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid())); "
            "sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); "
            f"target = {receiver.getsockname()!r}; "
            "exec(\"while True:\\n sender.sendto(b'workload', target)\\n time.sleep(0.02)\")"
        )
        parent_code = (
            "import pathlib, subprocess, sys, time; "
            f"subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
            f"pid_file = pathlib.Path({str(pid_file)!r}); "
            "exec('while not pid_file.exists(): time.sleep(0.005)'); "
            "print('framework started', flush=True); "
            + ("sys.exit(0)" if outcome == "parent_exits" else "time.sleep(30)")
        )
        output = tmp_path / "framework.log"
        if outcome == "interrupted":
            real_wait = subprocess.Popen.wait
            interrupted = False

            def interrupt_once(child: subprocess.Popen[bytes], timeout: float | None = None) -> int:
                nonlocal interrupted
                if not interrupted:
                    deadline = time.monotonic() + 3
                    while not pid_file.exists() and time.monotonic() < deadline:
                        time.sleep(0.005)
                    interrupted = True
                    raise KeyboardInterrupt
                return real_wait(child, timeout=timeout)

            monkeypatch.setattr(subprocess.Popen, "wait", interrupt_once)
        try:
            if outcome == "parent_exits":
                run_external_framework((sys.executable, "-c", parent_code), output, timeout_s=1)
            elif outcome == "interrupted":
                with pytest.raises(KeyboardInterrupt):
                    run_external_framework((sys.executable, "-c", parent_code), output, timeout_s=1)
            else:
                with pytest.raises(RunnerError, match="external framework failed"):
                    run_external_framework((sys.executable, "-c", parent_code), output, timeout_s=1)
            assert pid_file.exists(), "descendant must run before cleanup is tested"
            if outcome != "interrupted":
                assert output.read_text() == "framework started\n"
            # Drain traffic queued before return; any new datagram means the workload survived.
            receiver.setblocking(False)
            while True:
                try:
                    receiver.recv(1024)
                except BlockingIOError:
                    break
            receiver.settimeout(0.15)
            with pytest.raises(TimeoutError):
                receiver.recv(1024)
        finally:
            if pid_file.exists():
                with suppress(ProcessLookupError):
                    os.kill(int(pid_file.read_text()), signal.SIGKILL)
