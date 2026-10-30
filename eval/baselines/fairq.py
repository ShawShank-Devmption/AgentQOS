"""Seeded hashed DRR approximation on the owned choke_v1 Linux bottleneck."""

from __future__ import annotations

import ipaddress
import json
import logging
import struct
import subprocess
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import Any

from eval.baselines.fifo import running_fifo
from harness.runtime import RuntimeConfig
from harness.topology import HOSTS, SWITCH_NAME, topology_manifest

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FlowTuple:
    """Explicit planned directional IPv4 five-tuple, without any traffic labels."""

    src_ip: str
    dst_ip: str
    protocol: str
    src_port: int
    dst_port: int

    def __post_init__(self) -> None:
        if not isinstance(self.src_ip, str) or not isinstance(self.dst_ip, str):
            raise ValueError("flow IP addresses must be strings")
        ipaddress.IPv4Address(self.src_ip)
        ipaddress.IPv4Address(self.dst_ip)
        if self.protocol not in ("tcp", "udp"):
            raise ValueError("flow protocol must be tcp or udp")
        if any(
            type(port) is not int or not 1 <= port <= 65_535
            for port in (self.src_port, self.dst_port)
        ):
            raise ValueError("flow ports must be integers between 1 and 65535")


@dataclass(frozen=True)
class FairQueueConfig:
    """Explicit seed, bucket count, FIFO depth, quantum, and planned flow tuples."""

    seed: int
    buckets: int
    limit_packets: int
    quantum_bytes: int
    flows: tuple[FlowTuple, ...]

    def __post_init__(self) -> None:
        if type(self.seed) is not int or not 0 <= self.seed <= 0xFFFF_FFFF:
            raise ValueError("seed must be an unsigned 32-bit integer")
        if type(self.buckets) is not int or not 1 <= self.buckets <= 256:
            raise ValueError("buckets must be between 1 and 256")
        if any(
            type(value) is not int or value <= 0
            for value in (self.limit_packets, self.quantum_bytes)
        ):
            raise ValueError("limit_packets and quantum_bytes must be positive integers")
        if not isinstance(self.flows, tuple) or not self.flows or len(self.flows) > 65_533:
            raise ValueError("planned flows must be a nonempty tuple of at most 65533 entries")
        if not all(isinstance(flow, FlowTuple) for flow in self.flows):
            raise ValueError("planned flows must contain FlowTuple entries")
        if len(set(self.flows)) != len(self.flows):
            raise ValueError("duplicate flow tuple")


def bucket_for(flow: FlowTuple, config: FairQueueConfig) -> int:
    """Hash wire-order tuple bytes with an explicit CRC32 seed into a DRR bucket."""
    wire = ipaddress.IPv4Address(flow.src_ip).packed + ipaddress.IPv4Address(flow.dst_ip).packed
    wire += struct.pack("!BHH", 6 if flow.protocol == "tcp" else 17, flow.src_port, flow.dst_port)
    return zlib.crc32(wire, config.seed) % config.buckets + 1


def qdisc_commands(
    interface: str, link_mbps: int, delay_ms: int, config: FairQueueConfig
) -> list[list[str]]:
    """Build real tc argv preserving HTB rate and netem delay, with DRR/FIFO leaves.

    Unplanned, fragmented and non-IPv4 packets use bucket 1 through the final catch-all filter.
    This is a planned-flow approximation, not dynamic kernel SFQ or BMv2 queue emulation.
    """
    expected = (
        f"{SWITCH_NAME}-eth{next(host.switch_port for host in HOSTS if host.role == 'target')}"
    )
    if interface != expected:
        raise ValueError("interface must be the owned choke_v1 target bottleneck")
    topology_manifest(link_mbps)
    if type(delay_ms) is not int or delay_ms < 0:
        raise ValueError("delay_ms must be a nonnegative integer")
    dev = ["dev", interface]
    commands = [
        ["tc", "qdisc", "replace", *dev, "root", "handle", "1:", "htb", "default", "1"],
        [
            "tc",
            "class",
            "add",
            *dev,
            "parent",
            "1:",
            "classid",
            "1:1",
            "htb",
            "rate",
            f"{link_mbps}mbit",
            "ceil",
            f"{link_mbps}mbit",
        ],
        [
            "tc",
            "qdisc",
            "add",
            *dev,
            "parent",
            "1:1",
            "handle",
            "10:",
            "netem",
            "delay",
            f"{delay_ms}ms",
        ],
        ["tc", "qdisc", "add", *dev, "parent", "10:1", "handle", "20:", "drr"],
    ]
    for bucket in range(1, config.buckets + 1):
        commands.extend(
            [
                [
                    "tc",
                    "class",
                    "add",
                    *dev,
                    "parent",
                    "20:",
                    "classid",
                    f"20:{bucket:x}",
                    "drr",
                    "quantum",
                    str(config.quantum_bytes),
                ],
                [
                    "tc",
                    "qdisc",
                    "add",
                    *dev,
                    "parent",
                    f"20:{bucket:x}",
                    "pfifo",
                    "limit",
                    str(config.limit_packets),
                ],
            ]
        )
    for priority, flow in enumerate(config.flows, 1):
        commands.append(
            [
                "tc",
                "filter",
                "add",
                *dev,
                "parent",
                "20:",
                "protocol",
                "ip",
                "pref",
                str(priority),
                "flower",
                "src_ip",
                flow.src_ip,
                "dst_ip",
                flow.dst_ip,
                "ip_proto",
                flow.protocol,
                "src_port",
                str(flow.src_port),
                "dst_port",
                str(flow.dst_port),
                "classid",
                f"20:{bucket_for(flow, config):x}",
            ]
        )
    commands.append(
        [
            "tc",
            "filter",
            "add",
            *dev,
            "parent",
            "20:",
            "protocol",
            "all",
            "pref",
            str(len(config.flows) + 1),
            "matchall",
            "classid",
            "20:1",
        ]
    )
    return commands


def _run_tc(switch: Any, command: list[str]) -> None:
    child = switch.popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        _, stderr = child.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        child.kill()
        child.communicate()
        raise
    if child.returncode:
        raise RuntimeError(f"tc failed ({child.returncode}): {stderr.strip()}")


@contextmanager
def running_fairq(runtime: RuntimeConfig, config: FairQueueConfig) -> Iterator[Any]:
    """Install seeded hashed DRR only on an interface allocated by this FIFO context."""
    target = next(
        link for link in topology_manifest(runtime.link_mbps)["links"] if "bw_mbps" in link
    )
    interface = f"{SWITCH_NAME}-eth{target['switch_port']}"
    commands = qdisc_commands(interface, runtime.link_mbps, target["delay_ms"], config)
    with running_fifo(runtime) as network:
        switch = network.get(SWITCH_NAME)
        for command in commands:
            _run_tc(switch, command)
        metadata = {
            "system": "fairq",
            "mechanism": "Linux tc HTB/netem/seeded-hash DRR",
            "configuration": asdict(config),
            "commands": commands,
            "unplanned_flow_bucket": 1,
        }
        (runtime.output_dir / "fairq.json").write_text(json.dumps(metadata, indent=2) + "\n")
        # The outer owner destroys this newly allocated interface on success or partial failure.
        yield network
