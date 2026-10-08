"""Persistent Linux execution boundary for one storm experiment cell."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import asdict
from decimal import Decimal
from ipaddress import IPv4Address
from pathlib import Path
from typing import TYPE_CHECKING, Any

from common.contracts import TrafficClass
from dashboard.producer import build_snapshot
from eval.baselines.base import BaselinePlan, baseline_plan
from eval.metrics import completion_times, percentile_summary
from harness.capture import (
    CaptureWindow,
    CorpusVerification,
    label_pcap_flows,
    read_capture_windows,
    verify_corpus,
    write_capture_window,
    write_labels,
)
from harness.topology import (
    SWITCH_NAME,
    TARGET_NAME,
    SwitchLaunchConfig,
    TopologySession,
)

if TYPE_CHECKING:
    from harness.demo import StormConfig, StormPlan

SessionFactory = Callable[[SwitchLaunchConfig], AbstractContextManager[Any]]
NetworkRunner = Callable[[Any, "StormConfig", "StormPlan"], None]
RuntimeValidator = Callable[[str], None]
PortWaiter = Callable[[Any, str, int, Any], None]
ArtifactCollector = Callable[["StormConfig"], None]
LocalProcessStarter = Callable[[tuple[str, ...], Path], tuple[Any, Any]]
LOGGER = logging.getLogger(__name__)
AGENT_HOST_NAMES = ("h_agent1", "h_agent2", "h_agent3", "h_agent4")


class BaselineSession(AbstractContextManager["BaselineSession"]):
    """Apply one baseline inside the active topology and always remove it."""

    def __init__(self, network: Any, plan: BaselinePlan, output_dir: Path) -> None:
        self._network = network
        self._plan = plan
        self._output_dir = output_dir
        self._process: Any | None = None
        self._log_handle: Any | None = None
        self._active = False

    def __enter__(self) -> BaselineSession:
        self._active = True
        try:
            if self._plan.name == "app_limiter":
                prefix = self._output_dir / "nginx"
                (prefix / "logs").mkdir(parents=True, exist_ok=True)
                log_path = self._output_dir / "nginx.log"
                self._log_handle = log_path.open("w", encoding="utf-8")
                target = self._network.get(TARGET_NAME)
                self._process = target.popen(
                    self._plan.setup_commands[0],
                    stdout=self._log_handle,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                if self._process.poll() is not None:
                    raise RuntimeError(f"nginx exited during startup; see {log_path}")
            else:
                switch = self._network.get(SWITCH_NAME)
                for command in self._plan.setup_commands:
                    _run_checked(switch, command)
        except Exception:
            self._cleanup(suppress_errors=True)
            raise
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        del exc_value, traceback
        self._cleanup(suppress_errors=exc_type is not None)

    def _cleanup(self, *, suppress_errors: bool) -> None:
        if not self._active:
            return
        self._active = False
        cleanup_error: Exception | None = None
        host_name = TARGET_NAME if self._plan.name == "app_limiter" else SWITCH_NAME
        host = self._network.get(host_name)
        for command in self._plan.teardown_commands:
            try:
                _run_checked(host, command)
            except Exception as exc:
                cleanup_error = exc
                LOGGER.exception("baseline teardown failed: %s", command)
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=3)
        if self._log_handle is not None and not self._log_handle.closed:
            self._log_handle.close()
        if cleanup_error is not None and not suppress_errors:
            raise cleanup_error


def _run_checked(host: Any, command: tuple[str, ...]) -> None:
    try:
        process = host.popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout, stderr = process.communicate()
    except OSError as exc:
        raise RuntimeError(f"could not execute topology command: {command[0]}") from exc
    if process.returncode != 0:
        detail = stderr.strip() or stdout.strip() or "no diagnostic output"
        raise RuntimeError(f"topology command failed ({command[0]}): {detail}")


def run_storm(
    config: StormConfig,
    p4_json: Path,
    plan: StormPlan,
    *,
    session_factory: SessionFactory = TopologySession,
    network_runner: NetworkRunner | None = None,
) -> None:
    """Execute one storm while owning its programmed topology lifecycle."""
    if not p4_json.is_file():
        raise FileNotFoundError(p4_json)
    switch_config = SwitchLaunchConfig(
        p4_json=p4_json,
        link_mbps=config.link_mbps,
        switch_log=config.output_dir / "simple_switch.log",
    )
    with session_factory(switch_config) as network:
        (network_runner or run_network_storm)(network, config, plan)


def run_network_storm(
    network: Any,
    config: StormConfig,
    plan: StormPlan,
    *,
    validate_runtime: RuntimeValidator = lambda system: _validate_runtime(system),
    port_waiter: PortWaiter = lambda host, address, port, process: _wait_for_port(
        host, address, port, process
    ),
    artifact_collector: ArtifactCollector = lambda runtime_config: _collect_artifacts(
        runtime_config
    ),
    local_process_starter: LocalProcessStarter | None = None,
) -> None:
    """Run capture, target, human flow, and four agents inside an active topology."""
    validate_runtime(config.system)
    config.output_dir.mkdir(parents=True, exist_ok=True)
    target = network.get(TARGET_NAME)
    human = network.get("h_human")
    agent_hosts = tuple(network.get(name) for name in AGENT_HOST_NAMES)
    baseline = baseline_plan(config.system, config.link_mbps, config.output_dir)
    service_phase, human_phase, agent_phase = plan.phases
    backend_port = 8081 if config.system == "app_limiter" else 8080
    target_command = _replace_argument(service_phase.commands[0], "--port", str(backend_port))
    managed_processes: list[Any] = []
    log_handles: list[Any] = []
    pcap_path = config.output_dir / "traffic.pcap"
    telemetry_path = config.output_dir / "packet_telemetry.tsv"
    snapshot_path = config.output_dir / "live_metrics.json"
    start_local = local_process_starter or _start_local_logged_process

    with BaselineSession(network, baseline, config.output_dir):
        try:
            capture, capture_log = _start_logged_process(
                target,
                (
                    "tshark",
                    "-i",
                    f"{TARGET_NAME}-eth0",
                    "-w",
                    str(pcap_path),
                ),
                config.output_dir / "tshark.log",
            )
            managed_processes.append(capture)
            log_handles.append(capture_log)

            telemetry, telemetry_handles = _start_packet_telemetry(
                target,
                f"{TARGET_NAME}-eth0",
                telemetry_path,
                config.output_dir / "packet_telemetry.log",
            )
            managed_processes.append(telemetry)
            log_handles.extend(telemetry_handles)

            producer, producer_log = start_local(
                (
                    sys.executable,
                    "-m",
                    "dashboard.producer",
                    "--telemetry",
                    str(telemetry_path),
                    "--snapshot",
                    str(snapshot_path),
                ),
                config.output_dir / "dashboard_producer.log",
            )
            managed_processes.append(producer)
            log_handles.append(producer_log)
            _ensure_started(producer, "dashboard metric producer")

            dashboard, dashboard_log = start_local(
                service_phase.commands[1],
                config.output_dir / "dashboard_server.log",
            )
            managed_processes.append(dashboard)
            log_handles.append(dashboard_log)
            _ensure_started(dashboard, "dashboard server")

            target_process, target_log = _start_logged_process(
                target,
                target_command,
                config.output_dir / "mcp_target.log",
            )
            managed_processes.append(target_process)
            log_handles.append(target_log)
            if config.system == "app_limiter":
                port_waiter(target, "127.0.0.1", backend_port, target_process)
            port_waiter(agent_hosts[0], "10.0.0.100", 8080, target_process)

            iperf_server, iperf_server_log = _start_logged_process(
                target,
                ("iperf3", "-s", "-1"),
                config.output_dir / "iperf_server.log",
            )
            managed_processes.append(iperf_server)
            log_handles.append(iperf_server_log)

            human_start = Decimal(str(time.time()))
            human_process, human_log = _start_logged_process(
                human,
                human_phase.commands[0],
                config.output_dir / "human_iperf.json",
            )
            managed_processes.append(human_process)
            log_handles.append(human_log)

            agent_processes: list[tuple[str, Any]] = []
            for host, command in zip(agent_hosts, agent_phase.commands, strict=True):
                framework = command[2]
                process, log_handle = _start_logged_process(
                    host,
                    command,
                    config.output_dir / f"{framework.rsplit('.', 1)[-1]}.log",
                )
                managed_processes.append(process)
                log_handles.append(log_handle)
                agent_processes.append((framework, process))

            timeout_s = config.duration_s + 60
            _wait_success(human_process, "human iperf", timeout_s)
            human_end = Decimal(str(time.time()))
            write_capture_window(
                CaptureWindow(
                    source_ip=_human_source_ip(),
                    start_time_s=human_start,
                    end_time_s=human_end,
                    label=TrafficClass.HUMAN_INTERACTIVE,
                    source_framework="iperf-human",
                ),
                config.output_dir / "orchestration.jsonl",
            )
            for framework, process in agent_processes:
                _wait_success(process, framework, timeout_s)
        finally:
            for process in reversed(managed_processes):
                _stop_process(process)
            for handle in log_handles:
                if not handle.closed:
                    handle.close()
        artifact_collector(config)


def _replace_argument(command: tuple[str, ...], flag: str, value: str) -> tuple[str, ...]:
    arguments = list(command)
    try:
        index = arguments.index(flag)
    except ValueError as exc:
        raise RuntimeError(f"storm command omitted required flag: {flag}") from exc
    arguments[index + 1] = value
    return tuple(arguments)


def _start_logged_process(host: Any, command: tuple[str, ...], log_path: Path) -> tuple[Any, Any]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("w", encoding="utf-8")
    try:
        process = host.popen(
            command,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except OSError:
        log_handle.close()
        raise
    return process, log_handle


def _start_packet_telemetry(
    host: Any,
    interface: str,
    telemetry_path: Path,
    error_path: Path,
) -> tuple[Any, tuple[Any, Any]]:
    telemetry_path.parent.mkdir(parents=True, exist_ok=True)
    telemetry_handle = telemetry_path.open("w", encoding="utf-8")
    error_handle = error_path.open("w", encoding="utf-8")
    command = (
        "tshark",
        "-l",
        "-i",
        interface,
        "-Y",
        "ip",
        "-T",
        "fields",
        "-E",
        "separator=/t",
        "-E",
        "occurrence=f",
        "-e",
        "frame.time_epoch",
        "-e",
        "ip.src",
        "-e",
        "ip.dst",
        "-e",
        "frame.len",
        "-e",
        "tcp.analysis.ack_rtt",
    )
    try:
        process = host.popen(
            command,
            stdout=telemetry_handle,
            stderr=error_handle,
            text=True,
        )
    except OSError:
        telemetry_handle.close()
        error_handle.close()
        raise
    return process, (telemetry_handle, error_handle)


def _start_local_logged_process(
    command: tuple[str, ...],
    log_path: Path,
) -> tuple[subprocess.Popen[str], Any]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("w", encoding="utf-8")
    try:
        process = subprocess.Popen(  # noqa: S603
            command,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except OSError:
        log_handle.close()
        raise
    return process, log_handle


def _ensure_started(process: Any, name: str) -> None:
    time.sleep(0.05)
    return_code = process.poll()
    if return_code is not None:
        raise RuntimeError(f"{name} exited during startup with status {return_code}")


def _wait_success(process: Any, name: str, timeout_s: int) -> None:
    try:
        process.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{name} exceeded {timeout_s} seconds") from exc
    if process.returncode != 0:
        raise RuntimeError(f"{name} exited with status {process.returncode}")


def _stop_process(process: Any) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def _wait_for_port(
    host: Any,
    address: str,
    port: int,
    service_process: Any,
    timeout_s: float = 5.0,
) -> None:
    deadline = time.monotonic() + timeout_s
    script = (
        "import socket,sys; "
        "connection=socket.create_connection((sys.argv[1],int(sys.argv[2])),1); "
        "connection.close()"
    )
    while True:
        if service_process.poll() is not None:
            raise RuntimeError(f"service for {address}:{port} exited during startup")
        probe = host.popen(
            (sys.executable, "-c", script, address, str(port)),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        probe.wait(timeout=2)
        if probe.returncode == 0:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"service {address}:{port} was not ready after {timeout_s:g} seconds"
            )
        time.sleep(0.05)


def _validate_runtime(system: str) -> None:
    required = ["tshark", "iperf3", "tc"]
    if system == "app_limiter":
        required.append("nginx")
    missing = [command for command in required if shutil.which(command) is None]
    if missing:
        raise RuntimeError(f"missing storm runtime commands: {', '.join(missing)}")


def _collect_artifacts(config: StormConfig) -> None:
    pcap_path = config.output_dir / "traffic.pcap"
    orchestration_path = config.output_dir / "orchestration.jsonl"
    windows = read_capture_windows(orchestration_path)
    labels = label_pcap_flows(pcap_path, windows)
    if not labels:
        raise RuntimeError("storm capture produced no labeled flows")
    labels_path = config.output_dir / "labels.csv"
    write_labels(labels, labels_path)
    verification = verify_corpus(labels, windows)
    stats_path = config.output_dir / "corpus_stats.json"
    stats_path.write_text(
        json.dumps(asdict(verification), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if verification.verification_rate < 0.95:
        raise RuntimeError(
            f"corpus verification rate is below 95%: {verification.verification_rate:.3f}"
        )
    write_run_summary(config, windows, verification)


def write_run_summary(
    config: StormConfig,
    windows: tuple[CaptureWindow, ...],
    verification: CorpusVerification,
) -> None:
    """Write analysis-ready class and completion metrics for one run.

    Args:
        config: Coordinates for the completed storm cell.
        windows: Ground-truth orchestration intervals for the run.
        verification: Corpus traceability result for the captured pcap.
    """
    if not windows:
        raise RuntimeError("cannot summarize a run without orchestration windows")
    start_s = min(float(window.start_time_s) for window in windows)
    end_s = max(float(window.end_time_s) for window in windows)
    if end_s <= start_s:
        raise RuntimeError("run summary requires a positive measurement interval")
    snapshot = build_snapshot(
        config.output_dir / "packet_telemetry.tsv",
        now_s=end_s,
        window_s=end_s - start_s,
    )
    completion_samples = completion_times(config.output_dir / "mcp_requests.jsonl")
    if not completion_samples:
        raise RuntimeError("run summary requires at least one successful MCP completion")
    completion = percentile_summary(completion_samples)
    summary = {
        "system": config.system,
        "link_mbps": config.link_mbps,
        "agent_share_pct": config.agent_share_pct,
        "burst_intensity": config.burst_intensity,
        "duration_s": config.duration_s,
        "seed": config.seed,
        "measurement_start_s": start_s,
        "measurement_end_s": end_s,
        "generated_at": snapshot["generated_at"],
        "classes": snapshot["classes"],
        "tool_completion": asdict(completion),
        "corpus": asdict(verification),
    }
    (config.output_dir / "run_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _human_source_ip() -> IPv4Address:
    return IPv4Address("10.0.0.2")
