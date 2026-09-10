"""Single source of truth for the `choke_v1` Mininet topology."""

from dataclasses import asdict, dataclass
from typing import Any

from common.contracts import EXPERIMENT_TOPOLOGY, MAX_LINK_MBPS, MIN_LINK_MBPS

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
