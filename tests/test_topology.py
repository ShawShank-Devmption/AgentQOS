"""Tests for the deterministic and executable `choke_v1` topology."""

from pathlib import Path

import pytest

import harness.topology as topology
from controller.switch_api import SwitchApiError
from harness.topology import (
    HOSTS,
    SwitchLaunchConfig,
    forwarding_commands,
    run_smoke,
    topology_manifest,
)


def test_choke_v1_has_expected_roles_and_ports() -> None:
    manifest = topology_manifest(link_mbps=20)
    assert manifest["name"] == "choke_v1"
    assert manifest["switch"] == "s1"
    assert [host["role"] for host in manifest["hosts"]] == [
        "agent",
        "agent",
        "agent",
        "agent",
        "human",
        "background",
        "target",
    ]
    assert [host.switch_port for host in HOSTS] == [1, 2, 3, 4, 5, 6, 7]


def test_only_target_link_is_the_bottleneck() -> None:
    manifest = topology_manifest(link_mbps=35)
    bottlenecks = [link for link in manifest["links"] if "bw_mbps" in link]
    assert bottlenecks == [
        {
            "host": "h_target",
            "switch": "s1",
            "switch_port": 7,
            "bw_mbps": 35,
            "delay_ms": 1,
        }
    ]


@pytest.mark.parametrize("link_mbps", [0, 9, 51, 100])
def test_link_capacity_must_stay_in_designed_range(link_mbps: int) -> None:
    with pytest.raises(ValueError, match="between 10 and 50"):
        topology_manifest(link_mbps)


def test_forwarding_commands_program_flood_group_and_all_host_macs() -> None:
    assert forwarding_commands() == (
        "mc_mgrp_create 1",
        "mc_node_create 0 1 2 3 4 5 6 7",
        "mc_node_associate 1 0",
        "table_add tbl_l2_forward set_egress_port 00:00:00:00:00:01 => 1",
        "table_add tbl_l2_forward set_egress_port 00:00:00:00:00:04 => 2",
        "table_add tbl_l2_forward set_egress_port 00:00:00:00:00:05 => 3",
        "table_add tbl_l2_forward set_egress_port 00:00:00:00:00:06 => 4",
        "table_add tbl_l2_forward set_egress_port 00:00:00:00:00:02 => 5",
        "table_add tbl_l2_forward set_egress_port 00:00:00:00:00:03 => 6",
        "table_add tbl_l2_forward set_egress_port 00:00:00:00:00:64 => 7",
    )


def test_switch_launch_config_requires_compiled_p4_json(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        SwitchLaunchConfig(p4_json=tmp_path / "missing.json")


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class _RunningProcess:
    def poll(self) -> int | None:
        return None


class _ExitedProcess:
    def poll(self) -> int | None:
        return 1


class _EventuallyReadyApi:
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.reads = 0

    def read_register(self, register_name: str, index: int | None = None) -> tuple[int, ...]:
        self.reads += 1
        if self.reads <= self.failures:
            raise SwitchApiError("thrift unavailable")
        return (0,)


def test_switch_readiness_retries_thrift_until_register_is_readable() -> None:
    clock = _Clock()
    api = _EventuallyReadyApi(failures=2)

    topology._wait_for_switch_ready(
        api,
        _RunningProcess(),
        timeout_s=1.0,
        poll_interval_s=0.1,
        clock=clock,
        sleeper=clock.sleep,
    )

    assert api.reads == 3
    assert clock.now == pytest.approx(0.2)


def test_switch_readiness_fails_immediately_if_process_exits() -> None:
    clock = _Clock()

    with pytest.raises(RuntimeError, match="exited during startup"):
        topology._wait_for_switch_ready(
            _EventuallyReadyApi(failures=1),
            _ExitedProcess(),
            timeout_s=1.0,
            poll_interval_s=0.1,
            clock=clock,
            sleeper=clock.sleep,
        )

    assert clock.now == 0.0


def test_switch_readiness_times_out_with_specific_diagnostic() -> None:
    clock = _Clock()

    with pytest.raises(RuntimeError, match="not ready after 0.3 seconds"):
        topology._wait_for_switch_ready(
            _EventuallyReadyApi(failures=100),
            _RunningProcess(),
            timeout_s=0.3,
            poll_interval_s=0.1,
            clock=clock,
            sleeper=clock.sleep,
        )


class _FailingNetwork:
    def __init__(self) -> None:
        self.stopped = False

    def build(self) -> None:
        pass

    def start(self) -> None:
        raise RuntimeError("switch failed")

    def stop(self) -> None:
        self.stopped = True


class _RecordingNetwork:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def build(self) -> None:
        self._events.append("build")

    def start(self) -> None:
        self._events.append("start")

    def stop(self) -> None:
        self._events.append("stop")


class _RecordingSwitchApi:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def run_commands(self, commands: tuple[str, ...]) -> str:
        assert commands == forwarding_commands()
        self._events.append("program")
        return ""


def test_topology_session_builds_programs_and_always_stops(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    p4_json = tmp_path / "l2fwd.json"
    p4_json.write_text("{}", encoding="utf-8")
    events: list[str] = []
    network = _RecordingNetwork(events)
    monkeypatch.setattr("harness.topology._validate_linux_runtime", lambda: None)
    monkeypatch.setattr("harness.topology._create_network", lambda config: network)
    monkeypatch.setattr(
        "harness.topology.SwitchApi",
        lambda cli_path, thrift_port: _RecordingSwitchApi(events),
    )

    with topology.TopologySession(SwitchLaunchConfig(p4_json=p4_json)) as active:
        assert active is network
        assert events == ["build", "start", "program"]

    assert events == ["build", "start", "program", "stop"]


def test_run_smoke_stops_partially_started_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    p4_json = tmp_path / "l2fwd.json"
    p4_json.write_text("{}", encoding="utf-8")
    network = _FailingNetwork()
    monkeypatch.setattr("harness.topology._validate_linux_runtime", lambda: None)
    monkeypatch.setattr("harness.topology._create_network", lambda config: network)

    with pytest.raises(RuntimeError, match="switch failed"):
        run_smoke(SwitchLaunchConfig(p4_json=p4_json))

    assert network.stopped


class _BuildFailingNetwork(_FailingNetwork):
    def build(self) -> None:
        raise RuntimeError("network build failed")


def test_run_smoke_stops_network_when_explicit_build_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    p4_json = tmp_path / "l2fwd.json"
    p4_json.write_text("{}", encoding="utf-8")
    network = _BuildFailingNetwork()
    monkeypatch.setattr("harness.topology._validate_linux_runtime", lambda: None)
    monkeypatch.setattr("harness.topology._create_network", lambda config: network)

    with pytest.raises(RuntimeError, match="network build failed"):
        run_smoke(SwitchLaunchConfig(p4_json=p4_json))

    assert network.stopped


def test_run_smoke_rejects_unsupported_host(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    p4_json = tmp_path / "l2fwd.json"
    p4_json.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("harness.topology.platform.system", lambda: "Darwin")

    with pytest.raises(RuntimeError, match="requires Linux"):
        run_smoke(SwitchLaunchConfig(p4_json=p4_json))
