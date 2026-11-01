"""C1/G2 gateway contracts, command construction, and fail-open lifecycle."""

import json
import subprocess
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from common.contracts import (
    CONGESTION_HIGH_PACKETS,
    GATEWAY_CPU_INTERFACE,
    GATEWAY_LAN_PORT,
    GATEWAY_MAX_FRAME_BYTES,
    GATEWAY_PORTS,
    GATEWAY_TOPOLOGY,
    GATEWAY_WAN_PORT,
)
from eval.run_experiment import load_config
from harness import gateway


def _config(tmp_path: Path, system: str = "ours") -> gateway.GatewayConfig:
    experiment = replace(
        load_config(Path("eval/configs/review_1_smoke.yaml")),
        system=system,
        topology=GATEWAY_TOPOLOGY,
    )
    return gateway.GatewayConfig(experiment, tmp_path / "session", tmp_path / "scripts")


def test_gateway_maps_ports_and_cpu_without_mininet_interfaces(tmp_path: Path) -> None:
    config = _config(tmp_path)
    command = gateway.gateway_command(config)
    assert "1@gw-lan-sw" in command
    assert "4@gw-wan-sw" in command
    assert f"255@{GATEWAY_CPU_INTERFACE}" in command
    assert "--priority-queues" in command
    assert "--log-console" not in command
    assert all("s1-" not in word for word in command)
    fifo = gateway.gateway_command(_config(tmp_path, "fifo"))
    assert f"255@{GATEWAY_CPU_INTERFACE}" not in fifo
    assert any(word.endswith("fifo_gateway.json") for word in fifo)


def test_both_directions_shaped_and_flood_group_excludes_cpu() -> None:
    commands = gateway.gateway_policy_commands(20, "02:00:00:00:00:01", "02:00:00:00:00:02")
    assert "mc_node_create 0 1 4" in commands
    rate = 20_000_000 // (8 * GATEWAY_MAX_FRAME_BYTES)
    for port in GATEWAY_PORTS:
        assert f"set_queue_rate {rate} {port}" in commands
        assert f"set_queue_depth {4 * CONGESTION_HIGH_PACKETS} {port}" in commands
    assert commands[3].endswith(f"=> {GATEWAY_LAN_PORT}")
    assert commands[4].endswith(f"=> {GATEWAY_WAN_PORT}")


@pytest.mark.parametrize(
    "mac", ["garbage", "01:00:00:00:00:01", "00:00:00:00:00:00", "02:00:00:00:00:02"]
)
def test_invalid_or_duplicate_peer_mac_fails_before_policy(mac: str) -> None:
    with pytest.raises(ValueError):
        gateway.gateway_policy_commands(20, mac, "02:00:00:00:00:02")


def test_original_pipeline_is_rejected_before_handover(tmp_path: Path) -> None:
    artifact = tmp_path / "old.json"
    artifact.write_text(
        json.dumps({"pipelines": [{"name": "egress", "tables": []}], "register_arrays": []})
    )
    with pytest.raises(gateway.RuntimePrerequisiteError, match="reflection"):
        gateway.validate_gateway_pipeline(artifact, "fifo")


@pytest.mark.parametrize("failure", ["none", "start", "policy", "workload", "dead", "handover"])
def test_every_takeover_exit_stops_owned_switch_before_bypass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    config = _config(tmp_path)
    events = []
    child = Mock()
    child.args = ["simple_switch"]
    child.poll.return_value = 1 if failure == "dead" else None
    child.returncode = 1
    monkeypatch.setattr(
        gateway, "validate_prerequisites", lambda _: ("02:00:00:00:00:01", "02:00:00:00:00:02")
    )

    def run(command: list[str], **kwargs: object) -> Mock:
        events.append(" ".join([Path(command[0]).name, *command[1:]]))
        if failure == "handover" and command[-1] == "off":
            raise subprocess.CalledProcessError(1, command)
        return Mock()

    def launch(*args: object, **kwargs: object) -> Mock:
        events.append("start")
        if failure == "start":
            raise OSError("launch failed")
        return child

    monkeypatch.setattr(gateway.subprocess, "run", run)
    monkeypatch.setattr(gateway.subprocess, "Popen", launch)
    monkeypatch.setattr(gateway, "stop_process", lambda _: events.append("stop"))
    monkeypatch.setattr(gateway, "create_cpu_pair", lambda *args: events.append("cpu-create"))
    monkeypatch.setattr(gateway, "delete_cpu_pair", lambda *args: events.append("cpu-delete"))
    monkeypatch.setattr(gateway, "wait_for_switch", lambda _: None)
    api = Mock()
    if failure == "policy":
        api.execute.side_effect = RuntimeError("policy failed")
    monkeypatch.setattr(gateway, "SwitchApi", lambda **kwargs: api)
    monkeypatch.setattr(gateway, "switch_logging_supported", lambda: True)

    def execute() -> None:
        with gateway.running_gateway(config) as running:
            if failure == "workload":
                raise RuntimeError("traffic failed")
            if failure == "dead":
                gateway.monitor_gateway(running, 1)

    if failure == "none":
        execute()
    else:
        with pytest.raises((OSError, RuntimeError, subprocess.CalledProcessError)):
            execute()
    assert events[-1] == "bypass.sh on"
    if failure not in {"start", "handover"}:
        assert events.index("stop") < events.index("cpu-delete") < events.index("bypass.sh on")
    else:
        assert "stop" not in events


def test_missing_i3_scripts_fails_without_takeover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gateway.platform, "system", lambda: "Linux")
    monkeypatch.setattr(gateway.os, "geteuid", lambda: 0)
    monkeypatch.setattr(gateway.shutil, "which", lambda _: "/usr/bin/tool")
    run = Mock()
    monkeypatch.setattr(gateway.subprocess, "run", run)
    with pytest.raises(gateway.RuntimePrerequisiteError, match="I3 must provide"):
        gateway.validate_prerequisites(_config(tmp_path))
    run.assert_not_called()


def test_legacy_mininet_workload_rejects_gateway_before_allocating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from eval import workload

    monkeypatch.setenv("AAN_TOPOLOGY", GATEWAY_TOPOLOGY)
    with pytest.raises(ValueError, match="choke_v1 only"):
        workload.main()


def test_occupied_thrift_port_is_rejected_before_handover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    config.scripts.mkdir()
    path = config.scripts / "bypass.sh"
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    monkeypatch.setattr(gateway.platform, "system", lambda: "Linux")
    monkeypatch.setattr(gateway.os, "geteuid", lambda: 0)
    monkeypatch.setattr(gateway.shutil, "which", lambda _: "/usr/bin/tool")
    probe = Mock()
    probe.__enter__ = Mock(return_value=probe)
    probe.__exit__ = Mock(return_value=False)
    probe.bind.side_effect = OSError("occupied")
    monkeypatch.setattr(gateway.socket, "socket", lambda *args: probe)
    run = Mock()
    monkeypatch.setattr(gateway.subprocess, "run", run)
    with pytest.raises(gateway.RuntimePrerequisiteError, match="Thrift port"):
        gateway.validate_prerequisites(config)
    run.assert_not_called()


def test_gateway_requires_both_flags_and_compiled_reflection_guard(tmp_path: Path) -> None:
    from common.contracts import CONGESTION_FLAG_REGISTER, TABLE_NAMES

    path = tmp_path / "gateway.json"
    artifact = {
        "register_arrays": [{"name": CONGESTION_FLAG_REGISTER, "size": 2}],
        "pipelines": [
            {"name": "ingress", "tables": [{"name": name} for name in TABLE_NAMES]},
            {
                "name": "egress",
                "tables": [],
                "conditionals": [{"expression": {"left": "ingress_port", "right": "egress_port"}}],
            },
        ],
    }
    path.write_text(json.dumps(artifact))
    gateway.validate_gateway_pipeline(path, "ours")
    artifact["register_arrays"][0]["size"] = 1
    path.write_text(json.dumps(artifact))
    with pytest.raises(gateway.RuntimePrerequisiteError, match="separate LAN/WAN"):
        gateway.validate_gateway_pipeline(path, "ours")


def test_dead_switch_health_monitor_does_not_wait_for_session_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child = Mock()
    child.poll.return_value = 1
    child.returncode = 1
    sleep = Mock()
    monkeypatch.setattr(gateway.time, "sleep", sleep)
    with pytest.raises(RuntimeError, match="exited"):
        gateway.monitor_gateway(child, 120)
    sleep.assert_not_called()


def test_aggregate_caps_use_both_owned_interfaces_and_contract_queue_bounds() -> None:
    commands = gateway.aggregate_shaping_commands(10)
    assert len(commands) == 2
    assert {command[4] for command in commands} == {"gw-lan-sw", "gw-wan-sw"}
    for command in commands:
        assert command[command.index("rate") + 1] == "10mbit"
        assert command[command.index("burst") + 1] == str(
            CONGESTION_HIGH_PACKETS * GATEWAY_MAX_FRAME_BYTES
        )
    with pytest.raises(ValueError):
        gateway.aggregate_shaping_commands(100)


def test_infrastructure_namespace_is_read_as_a_literal(tmp_path: Path) -> None:
    settings = tmp_path / "gateway.env"
    settings.write_text("AAN_NETNS=aan-lan\n")
    assert gateway.infrastructure_namespace(settings) == "aan-lan"
    for invalid in ("AAN_NETNS=$(touch /tmp/unwanted)\n", "AAN_NETNS=one\nAAN_NETNS=two\n", ""):
        settings.write_text(invalid)
        with pytest.raises(gateway.RuntimePrerequisiteError):
            gateway.infrastructure_namespace(settings)
