"""Single source of truth for the `choke_v1` Mininet topology."""

from dataclasses import asdict, dataclass
from typing import Any

from common.contracts import (
    CONGESTION_HIGH_PACKETS,
    CPU_PORT,
    EXPERIMENT_TOPOLOGY,
    GATEWAY_CPU_INTERFACE,
    GATEWAY_LAN_INTERFACE,
    GATEWAY_MAX_FRAME_BYTES,
    GATEWAY_PORTS,
    GATEWAY_TOPOLOGY,
    GATEWAY_WAN_INTERFACE,
    MAX_LINK_MBPS,
    MIN_LINK_MBPS,
)

SWITCH_NAME = "s1"


@dataclass(frozen=True)
class HostSpec:
    """A host attached directly to the choke-point switch."""

    name: str
    role: str
    ip_address: str
    mac_address: str
    switch_port: int


HOSTS = (
    HostSpec("h_agent1", "agent", "10.0.0.1/24", "00:00:00:00:00:01", 1),
    HostSpec("h_human", "human", "10.0.0.2/24", "00:00:00:00:00:02", 2),
    HostSpec("h_bulk", "background", "10.0.0.3/24", "00:00:00:00:00:03", 3),
    HostSpec("h_target", "target", "10.0.0.100/24", "00:00:00:00:00:64", 4),
)


def topology_manifest(link_mbps: int = 20) -> dict[str, Any]:
    """Return the deterministic topology description used by tests and launch code.

    Args:
        link_mbps: Capacity of the switch-to-target bottleneck in Mbps.

    Returns:
        JSON-compatible host, switch, and link metadata.

    Raises:
        ValueError: If the bottleneck falls outside the designed 10-50 Mbps range.
    """
    if link_mbps < MIN_LINK_MBPS or link_mbps > MAX_LINK_MBPS:
        raise ValueError(f"link_mbps must be between {MIN_LINK_MBPS} and {MAX_LINK_MBPS}")

    links = []
    for host in HOSTS:
        link: dict[str, Any] = {
            "host": host.name,
            "switch": SWITCH_NAME,
            "switch_port": host.switch_port,
        }
        if host.role == "target":
            link.update({"bw_mbps": link_mbps, "delay_ms": 1})
        links.append(link)

    return {
        "name": EXPERIMENT_TOPOLOGY,
        "switch": SWITCH_NAME,
        "hosts": [asdict(host) for host in HOSTS],
        "links": links,
    }


def gateway_manifest(link_mbps: int) -> dict[str, Any]:
    """Describe C1's inline topology without constructing or modifying I3's network."""
    if type(link_mbps) is not int:
        raise ValueError("link_mbps must be an integer")
    topology_manifest(link_mbps)
    return {
        "name": GATEWAY_TOPOLOGY,
        "ports": [
            {"port": port, "interface": interface, "congestion_index": index}
            for index, (port, interface) in enumerate(
                zip(GATEWAY_PORTS, (GATEWAY_LAN_INTERFACE, GATEWAY_WAN_INTERFACE), strict=True)
            )
        ],
        "cpu_port": CPU_PORT,
        "cpu_interface": GATEWAY_CPU_INTERFACE,
        "queue_rate_pps": link_mbps * 1_000_000 // (8 * GATEWAY_MAX_FRAME_BYTES),
        "queue_depth_packets": 4 * CONGESTION_HIGH_PACKETS,
        "link_mbps": link_mbps,
    }


def build_topology(link_mbps: int = 20) -> object:
    """Build the `choke_v1` Mininet topology in the supported Linux environment.

    Args:
        link_mbps: Capacity of the switch-to-target bottleneck in Mbps.

    Returns:
        A configured `mininet.topo.Topo` instance.

    Raises:
        RuntimeError: If Mininet is unavailable on the current host.
        ValueError: If `link_mbps` is outside the designed range.
    """
    manifest = topology_manifest(link_mbps)
    try:
        from mininet.topo import Topo
    except ImportError as exc:
        raise RuntimeError(
            "Mininet is required; run this inside the Linux VM from docs/ENVIRONMENT.md"
        ) from exc

    topology = Topo()
    topology.addSwitch(SWITCH_NAME)
    for host in HOSTS:
        topology.addHost(host.name, ip=host.ip_address, mac=host.mac_address)
    for link in manifest["links"]:
        options = {"port2": link["switch_port"]}
        if "bw_mbps" in link:
            options.update({"bw": link["bw_mbps"], "delay": f"{link['delay_ms']}ms"})
        topology.addLink(link["host"], SWITCH_NAME, **options)
    return topology
