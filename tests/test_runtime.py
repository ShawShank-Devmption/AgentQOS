"""Host-side proof for Linux BMv2 launch boundaries and cleanup."""

import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from common.contracts import (
    CLASS_TREATMENTS,
    CONGESTION_HIGH_PACKETS,
    CPU_CLONE_SESSION,
    CPU_CONTROLLER_INTERFACE,
    CPU_PORT,
    CPU_SWITCH_INTERFACE,
    POLICY_VERSION_REGISTER,
)
from harness import runtime
from harness.runtime import (
    RuntimeConfig,
    RuntimePrerequisiteError,
    configure_switch_queues,
    install_l2_forwarding,
    run_smoke,
    stop_process,
    switch_command,
    validate_prerequisites,
)
from harness.topology import HOSTS


def test_runtime_prerequisites_fail_before_network_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "l2fwd.json"
    artifact.write_text("{}")
    monkeypatch.setattr("harness.runtime.platform.system", lambda: "Darwin")
    with pytest.raises(RuntimePrerequisiteError, match="Linux"):
        validate_prerequisites(RuntimeConfig(artifact, tmp_path / "out"))
    with pytest.raises(ValueError):
        RuntimeConfig(artifact, tmp_path / "out", thrift_port=0)


def test_forwarding_uses_all_explicit_ports_and_multicast() -> None:
    api = Mock()
    install_l2_forwarding(api)
    commands = api.execute.call_args.args[0]
    assert "mc_mgrp_create 1" in commands
    assert f"mc_node_create 0 {' '.join(str(host.switch_port) for host in HOSTS)}" in commands
    for host in HOSTS:
        assert any(
            host.mac_address in command and str(host.switch_port) == command.split()[-1]
            for command in commands
        )


def test_stop_escalates_and_reaps() -> None:
    child = Mock()
    child.poll.return_value = None
    child.wait.side_effect = [subprocess.TimeoutExpired("test", 1), 0]
    stop_process(child)
    child.terminate.assert_called_once()
    child.kill.assert_called_once()
    assert child.wait.call_count == 2


def test_bmv2_starts_enough_priority_queues_for_every_frozen_class(tmp_path: Path) -> None:
    config = RuntimeConfig(tmp_path / "agent_aware.json", tmp_path / "out")
    argv = switch_command(config, [(1, "s1-eth1"), (4, "s1-eth4")], cpu_enabled=True)
    assert argv[-3:] == [
        "--",
        "--priority-queues",
        str(max(t.queue_priority for t in CLASS_TREATMENTS.values()) + 1),
    ]
    assert "255@s1-cpu" in argv


def test_workload_configures_a_switch_bottleneck_above_hysteresis_depth(
    tmp_path: Path,
) -> None:
    config = RuntimeConfig(tmp_path / "agent_aware.json", tmp_path / "out", queue_rate_pps=1000)
    api = Mock()
    configure_switch_queues(api, config)
    commands = api.execute.call_args.args[0]
    assert f"set_queue_depth {4 * CONGESTION_HIGH_PACKETS} 4" in commands
    assert "set_queue_rate 1000 4" in commands
    with pytest.raises(ValueError):
        RuntimeConfig(tmp_path / "agent_aware.json", tmp_path / "out", queue_rate_pps=0)


def test_network_cleanup_after_programming_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = RuntimeConfig(tmp_path / "l2fwd.json", tmp_path / "out")
    network = Mock()
    monkeypatch.setattr("harness.runtime.validate_prerequisites", lambda config: None)
    monkeypatch.setattr("harness.runtime.create_network", lambda config: network)
    monkeypatch.setattr("harness.runtime.wait_for_switch", lambda *args: None)
    monkeypatch.setattr(
        "harness.runtime.install_l2_forwarding", Mock(side_effect=RuntimeError("bad"))
    )
    with pytest.raises(RuntimeError, match="bad"):
        run_smoke(config)
    network.stop.assert_called_once()
    assert json.loads((config.output_dir / "smoke.json").read_text())["status"] == "failed"


def test_smoke_reads_live_policy_register_and_iperf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = RuntimeConfig(tmp_path / "l2fwd.json", tmp_path / "out")
    network = Mock()
    api = Mock()
    api.read_register.return_value = (7,)
    result = {"end": {"sum_received": {"bits_per_second": 1_000_000}}}
    client = Mock()
    client.communicate.return_value = (json.dumps(result), "")
    client.returncode = 0
    network.get.return_value.popen.return_value = client
    monkeypatch.setattr("harness.runtime.validate_prerequisites", lambda config: None)
    monkeypatch.setattr("harness.runtime.create_network", lambda config: network)
    monkeypatch.setattr("harness.runtime.wait_for_switch", lambda *args: None)
    monkeypatch.setattr("harness.runtime.SwitchApi", lambda **kwargs: api)
    monkeypatch.setattr("harness.runtime._wait_for_iperf", lambda *args: None)
    result = run_smoke(config)
    assert result["policy_version"] == 7
    api.read_register.assert_called_with(POLICY_VERSION_REGISTER, 0)
    network.stop.assert_called_once()
    assert (config.output_dir / "iperf-client.json").exists()


def test_occupied_thrift_port_is_rejected_before_network_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "pipeline.json"
    artifact.write_text('{"pipelines": []}')
    monkeypatch.setattr("harness.runtime.platform.system", lambda: "Linux")
    monkeypatch.setattr("harness.runtime.os.geteuid", lambda: 0)
    monkeypatch.setattr("harness.runtime.shutil.which", lambda name: f"/bin/{name}")
    monkeypatch.setattr("harness.runtime.importlib.util.find_spec", lambda name: object())
    listener = Mock()
    listener.__enter__ = Mock(return_value=listener)
    listener.__exit__ = Mock(return_value=False)
    listener.bind.side_effect = OSError("address in use")
    monkeypatch.setattr("harness.runtime.socket.socket", lambda *args: listener)
    with pytest.raises(RuntimePrerequisiteError, match="Thrift port"):
        validate_prerequisites(RuntimeConfig(artifact, tmp_path / "out"))


def test_failed_cleanup_preserves_original_startup_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = RuntimeConfig(tmp_path / "l2fwd.json", tmp_path / "out")
    network = Mock()
    network.start.side_effect = RuntimeError("startup failed")
    network.stop.side_effect = RuntimeError("cleanup failed")
    monkeypatch.setattr("harness.runtime.validate_prerequisites", lambda config: None)
    monkeypatch.setattr("harness.runtime.create_network", lambda config: network)
    with pytest.raises(RuntimeError, match="startup failed"):
        run_smoke(config)
    manifest = json.loads((config.output_dir / "smoke.json").read_text())
    assert manifest["status"] == "failed"
    assert manifest["cleanup_errors"] == ["cleanup failed"]


def test_cpu_port_is_required_only_for_pipeline_with_punt_table(tmp_path: Path) -> None:
    l2 = tmp_path / "l2.json"
    agent = tmp_path / "agent.json"
    l2.write_text('{"pipelines": [{"tables": [{"name": "tbl_l2_forward"}]}]}')
    agent.write_text('{"pipelines": [{"tables": [{"name": "IngressPipeline.tbl_punt_filter"}]}]}')
    assert runtime.cpu_port_required(l2) is False
    assert runtime.cpu_port_required(agent) is True


def test_cpu_pair_creation_and_cleanup_use_only_frozen_interfaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> None:
        calls.append(command)

    monkeypatch.setattr("harness.runtime.subprocess.run", run)
    runtime.create_cpu_pair()
    runtime.delete_cpu_pair()
    assert calls == [
        [
            "ip",
            "link",
            "add",
            CPU_SWITCH_INTERFACE,
            "type",
            "veth",
            "peer",
            "name",
            CPU_CONTROLLER_INTERFACE,
        ],
        ["ip", "link", "set", "dev", CPU_SWITCH_INTERFACE, "up"],
        ["ip", "link", "set", "dev", CPU_CONTROLLER_INTERFACE, "up"],
        ["ip", "link", "delete", CPU_SWITCH_INTERFACE],
    ]


def test_cpu_mirror_session_uses_frozen_session_and_port() -> None:
    api = Mock()
    runtime.install_cpu_mirror(api)
    api.execute.assert_called_once_with([f"mirroring_add {CPU_CLONE_SESSION} {CPU_PORT}"])


def test_partial_cpu_pair_startup_deletes_owned_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> None:
        calls.append(command)
        if command[-1] == "up" or command[2] == "delete":
            raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr("harness.runtime.subprocess.run", run)
    with pytest.raises(subprocess.CalledProcessError) as error:
        runtime.create_cpu_pair()
    assert error.value.cmd[-1] == "up"
    assert calls[-1] == ["ip", "link", "delete", CPU_SWITCH_INTERFACE]


class _FakeHost:
    """Mininet host boundary: records argv run in the host namespace."""

    def __init__(self, exit_code: int = 0) -> None:
        self.commands: list[list[str]] = []
        self._exit_code = exit_code

    def defaultIntf(self) -> Mock:  # noqa: N802 - Mininet's API name
        interface = Mock()
        interface.name = "h_human-eth0"
        return interface

    def pexec(self, command: list[str]) -> tuple[str, str, int]:
        self.commands.append(command)
        return "", "Operation not supported" if self._exit_code else "", self._exit_code


def test_host_finishes_checksums_itself_so_bmv2_forwards_valid_frames() -> None:
    # Live BMv2 evidence: with veth TX offload on, every UDP datagram arrived with a bad
    # checksum and was dropped by the receiver; with rx/tx/sg off, 20 of 20 arrived intact.
    host = _FakeHost()
    runtime.disable_checksum_offload(host)
    assert host.commands == [
        ["ethtool", "--offload", "h_human-eth0", "rx", "off", "tx", "off", "sg", "off"]
    ]


def test_offload_failure_stops_the_run_instead_of_measuring_dropped_packets() -> None:
    with pytest.raises(RuntimePrerequisiteError, match="h_human-eth0.*Operation not supported"):
        runtime.disable_checksum_offload(_FakeHost(exit_code=1))


def test_missing_ethtool_is_rejected_before_network_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("harness.runtime.platform.system", lambda: "Linux")
    monkeypatch.setattr("harness.runtime.os.geteuid", lambda: 0)
    monkeypatch.setattr(
        "harness.runtime.shutil.which", lambda name: None if name == "ethtool" else f"/bin/{name}"
    )
    with pytest.raises(RuntimePrerequisiteError, match="ethtool"):
        validate_prerequisites(RuntimeConfig(tmp_path / "l2fwd.json", tmp_path / "out"))
