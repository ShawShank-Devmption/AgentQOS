"""Tests for the deterministic and executable `choke_v1` topology."""

from pathlib import Path

import pytest

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
        "human",
        "background",
        "target",
    ]
    assert [host.switch_port for host in HOSTS] == [1, 2, 3, 4]


def test_only_target_link_is_the_bottleneck() -> None:
    manifest = topology_manifest(link_mbps=35)
    bottlenecks = [link for link in manifest["links"] if "bw_mbps" in link]
    assert bottlenecks == [
        {
            "host": "h_target",
            "switch": "s1",
            "switch_port": 4,
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
        "mc_node_create 0 1 2 3 4",
        "mc_node_associate 1 0",
        "table_add tbl_l2_forward set_egress_port 00:00:00:00:00:01 => 1",
        "table_add tbl_l2_forward set_egress_port 00:00:00:00:00:02 => 2",
        "table_add tbl_l2_forward set_egress_port 00:00:00:00:00:03 => 3",
        "table_add tbl_l2_forward set_egress_port 00:00:00:00:00:64 => 4",
    )


def test_switch_launch_config_requires_compiled_p4_json(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        SwitchLaunchConfig(p4_json=tmp_path / "missing.json")


class _FailingNetwork:
    def __init__(self) -> None:
        self.stopped = False

    def start(self) -> None:
        raise RuntimeError("switch failed")

    def stop(self) -> None:
        self.stopped = True


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


def test_run_smoke_rejects_unsupported_host(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    p4_json = tmp_path / "l2fwd.json"
    p4_json.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("harness.topology.platform.system", lambda: "Darwin")

    with pytest.raises(RuntimeError, match="requires Linux"):
        run_smoke(SwitchLaunchConfig(p4_json=p4_json))
