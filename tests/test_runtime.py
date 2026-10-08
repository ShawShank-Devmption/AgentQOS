"""Tests for the persistent Linux storm runtime lifecycle."""

import json
from contextlib import AbstractContextManager
from decimal import Decimal
from ipaddress import IPv4Address
from pathlib import Path
from typing import Any

import pytest

import harness.runtime as runtime
from common.contracts import TrafficClass
from eval.baselines.base import baseline_plan
from harness.capture import CaptureWindow, CorpusVerification
from harness.demo import StormConfig, build_storm_plan
from harness.runtime import run_storm
from harness.topology import SwitchLaunchConfig


def _config(tmp_path: Path) -> StormConfig:
    task_script = tmp_path / "tasks.json"
    task_script.write_text(
        json.dumps([{"tool": "compute", "arguments": {"expression": "1+1"}}]),
        encoding="utf-8",
    )
    return StormConfig(
        system="fifo",
        link_mbps=20,
        agent_share_pct=30,
        burst_intensity="low",
        duration_s=30,
        seed=1,
        output_dir=tmp_path / "run",
        task_script=task_script,
    )


class _Session(AbstractContextManager[object]):
    def __init__(self, events: list[str]) -> None:
        self._events = events
        self.network = object()

    def __enter__(self) -> object:
        self._events.append("topology-start")
        return self.network

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        del exc_type, exc_value, traceback
        self._events.append("topology-stop")


def test_run_storm_owns_topology_around_network_workload(tmp_path: Path) -> None:
    config = _config(tmp_path)
    plan = build_storm_plan(config)
    p4_json = tmp_path / "l2fwd.json"
    p4_json.write_text("{}", encoding="utf-8")
    events: list[str] = []
    session = _Session(events)

    def session_factory(switch_config: SwitchLaunchConfig) -> _Session:
        assert switch_config.p4_json == p4_json
        assert switch_config.link_mbps == 20
        return session

    def network_runner(network: Any, runtime_config: StormConfig, runtime_plan: object) -> None:
        assert network is session.network
        assert runtime_config is config
        assert runtime_plan is plan
        events.append("workload")

    run_storm(
        config,
        p4_json,
        plan,
        session_factory=session_factory,
        network_runner=network_runner,
    )

    assert events == ["topology-start", "workload", "topology-stop"]


def test_run_storm_stops_topology_when_workload_fails(tmp_path: Path) -> None:
    config = _config(tmp_path)
    p4_json = tmp_path / "l2fwd.json"
    p4_json.write_text("{}", encoding="utf-8")
    events: list[str] = []

    def fail_workload(network: Any, runtime_config: StormConfig, runtime_plan: object) -> None:
        del network, runtime_config, runtime_plan
        events.append("workload")
        raise RuntimeError("traffic failed")

    with pytest.raises(RuntimeError, match="traffic failed"):
        run_storm(
            config,
            p4_json,
            build_storm_plan(config),
            session_factory=lambda switch_config: _Session(events),
            network_runner=fail_workload,
        )

    assert events == ["topology-start", "workload", "topology-stop"]


class _Process:
    def __init__(self, running: bool = False, exit_code: int = 0) -> None:
        self.exit_code = exit_code
        self.returncode: int | None = None if running else exit_code
        self.terminated = False

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        del timeout
        self.returncode = self.exit_code
        return "", ""

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        if self.returncode is None:
            self.returncode = self.exit_code
        return self.returncode


class _Host:
    def __init__(self, name: str, events: list[str], failed_module: str | None = None) -> None:
        self.name = name
        self.events = events
        self.failed_module = failed_module
        self.commands: list[tuple[str, ...]] = []
        self.background: list[_Process] = []

    def popen(self, command: tuple[str, ...], **kwargs: object) -> _Process:
        del kwargs
        self.commands.append(command)
        is_background = (
            (command[0] == "nginx" and "daemon off;" in command)
            or command[0] == "tshark"
            or (command[0] == "iperf3" and "-s" in command)
            or "harness.mcp_target.server" in command
        )
        exit_code = int(self.failed_module is not None and self.failed_module in command)
        self.events.append(f"{self.name}:{command[0]}:{'background' if is_background else 'run'}")
        process = _Process(running=is_background, exit_code=exit_code)
        if is_background:
            self.background.append(process)
        return process


class _Network:
    def __init__(self, events: list[str]) -> None:
        self.hosts = {
            "s1": _Host("s1", events),
            "h_target": _Host("h_target", events),
            "h_human": _Host("h_human", events),
            "h_agent1": _Host("h_agent1", events),
            "h_agent2": _Host("h_agent2", events),
            "h_agent3": _Host("h_agent3", events),
            "h_agent4": _Host("h_agent4", events),
        }

    def get(self, name: str) -> _Host:
        return self.hosts[name]


def test_queue_baseline_runs_on_switch_after_topology_and_tears_down(tmp_path: Path) -> None:
    events: list[str] = []
    network = _Network(events)
    plan = baseline_plan("fifo", 20, tmp_path)

    with runtime.BaselineSession(network, plan, tmp_path):
        events.append("workload")

    assert events == ["s1:tc:run", "workload", "s1:tc:run"]


def test_app_limiter_runs_in_target_namespace_and_is_cleaned_up(tmp_path: Path) -> None:
    events: list[str] = []
    network = _Network(events)
    plan = baseline_plan("app_limiter", 20, tmp_path)

    with runtime.BaselineSession(network, plan, tmp_path):
        events.append("workload")

    target = network.get("h_target")
    assert events == ["h_target:nginx:background", "workload", "h_target:nginx:run"]
    assert len(target.background) == 1
    assert target.background[0].terminated
    assert (tmp_path / "nginx" / "logs").is_dir()


def test_network_storm_runs_capture_services_human_and_all_agents(tmp_path: Path) -> None:
    config = _config(tmp_path)
    plan = build_storm_plan(config)
    events: list[str] = []
    network = _Network(events)
    local_commands: list[tuple[str, ...]] = []
    local_handles: list[Any] = []

    def start_local(command: tuple[str, ...], log_path: Path) -> tuple[_Process, object]:
        local_commands.append(command)
        handle = log_path.open("w", encoding="utf-8")
        local_handles.append(handle)
        return _Process(running=True), handle

    runtime.run_network_storm(
        network,
        config,
        plan,
        validate_runtime=lambda system: None,
        port_waiter=lambda host, address, port, process: events.append(
            f"ready:{host.name}:{address}:{port}"
        ),
        artifact_collector=lambda runtime_config: events.append("collect"),
        local_process_starter=start_local,
    )

    target_commands = network.get("h_target").commands
    human_commands = network.get("h_human").commands
    agent_commands = [
        command for index in range(1, 5) for command in network.get(f"h_agent{index}").commands
    ]
    assert sum(command[0] == "tshark" for command in target_commands) == 2
    assert any("tcp.analysis.ack_rtt" in command for command in target_commands)
    assert any("harness.mcp_target.server" in command for command in target_commands)
    assert any(command[:2] == ("iperf3", "-s") for command in target_commands)
    assert any(command[:2] == ("iperf3", "-c") for command in human_commands)
    assert [command[2] for command in agent_commands] == [
        "harness.agents.browser_use",
        "harness.agents.playwright_agent",
        "harness.agents.autogen",
        "harness.agents.claude_mcp",
    ]
    assert "ready:h_agent1:10.0.0.100:8080" in events
    assert [command[2] for command in local_commands] == [
        "dashboard.producer",
        "dashboard.server",
    ]
    assert all(handle.closed for handle in local_handles)
    assert events[-2:] == ["collect", "s1:tc:run"]
    assert (config.output_dir / "orchestration.jsonl").is_file()


def test_network_storm_cleans_up_when_an_agent_fails(tmp_path: Path) -> None:
    config = _config(tmp_path)
    events: list[str] = []
    network = _Network(events)
    network.hosts["h_agent3"] = _Host(
        "h_agent3",
        events,
        failed_module="harness.agents.autogen",
    )
    collected: list[str] = []

    def start_local(command: tuple[str, ...], log_path: Path) -> tuple[_Process, object]:
        del command
        return _Process(running=True), log_path.open("w", encoding="utf-8")

    with pytest.raises(RuntimeError, match="autogen exited with status 1"):
        runtime.run_network_storm(
            network,
            config,
            build_storm_plan(config),
            validate_runtime=lambda system: None,
            port_waiter=lambda host, address, port, process: None,
            artifact_collector=lambda runtime_config: collected.append("collect"),
            local_process_starter=start_local,
        )

    target = network.get("h_target")
    assert collected == []
    assert target.background
    assert all(process.terminated for process in target.background)
    assert events[-1] == "s1:tc:run"


def test_run_summary_retains_coordinates_class_metrics_and_completion_times(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    config.output_dir.mkdir()
    (config.output_dir / "packet_telemetry.tsv").write_text(
        "10.0\t10.0.0.2\t10.0.0.100\t1000\t0.010\n"
        "11.0\t10.0.0.1\t10.0.0.100\t500\t0.020\n"
        "12.0\t10.0.0.6\t10.0.0.100\t250\t0.030\n",
        encoding="utf-8",
    )
    (config.output_dir / "mcp_requests.jsonl").write_text(
        json.dumps(
            {
                "start_time_ns": 1_000_000_000,
                "end_time_ns": 1_025_000_000,
                "status": "ok",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    windows = (
        CaptureWindow(
            IPv4Address("10.0.0.2"),
            Decimal("10"),
            Decimal("15"),
            TrafficClass.HUMAN_INTERACTIVE,
            "iperf-human",
        ),
    )
    verification = CorpusVerification(3, 3, 1.0, {"iperf-human": 1})

    runtime.write_run_summary(config, windows, verification)

    summary = json.loads((config.output_dir / "run_summary.json").read_text(encoding="utf-8"))
    assert summary["system"] == "fifo"
    assert summary["agent_share_pct"] == 30
    assert summary["classes"]["HUMAN_INTERACTIVE"]["p99_ms"] == pytest.approx(10.0)
    assert summary["tool_completion"]["count"] == 1
    assert summary["tool_completion"]["p99_ms"] == pytest.approx(25.0)
    assert summary["corpus"]["verification_rate"] == 1.0
