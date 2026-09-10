"""Tests for the deterministic `choke_v1` topology contract."""

import pytest

from harness.topology import HOSTS, topology_manifest


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
