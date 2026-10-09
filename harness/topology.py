"""Single source of truth and Linux launcher for the `choke_v1` topology."""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import shutil
import subprocess
import time
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from common.contracts import (
    EXPERIMENT_TOPOLOGY,
    MAX_LINK_MBPS,
    MIN_LINK_MBPS,
    POLICY_VERSION_REGISTER,
)
from controller.switch_api import SwitchApi, SwitchApiError

SWITCH_NAME = "s1"
TARGET_NAME = "h_target"
HUMAN_NAME = "h_human"
FLOOD_GROUP = 1
LOGGER = logging.getLogger(__name__)


class _ProcessProbe(Protocol):
    def poll(self) -> int | None: ...


class _RegisterReader(Protocol):
    def read_register(self, register_name: str, index: int | None = None) -> tuple[int, ...]: ...


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
    HostSpec("h_agent2", "agent", "10.0.0.4/24", "00:00:00:00:00:04", 2),
    HostSpec("h_agent3", "agent", "10.0.0.5/24", "00:00:00:00:00:05", 3),
    HostSpec("h_agent4", "agent", "10.0.0.6/24", "00:00:00:00:00:06", 4),
    HostSpec("h_human", "human", "10.0.0.2/24", "00:00:00:00:00:02", 5),
    HostSpec("h_bulk", "background", "10.0.0.3/24", "00:00:00:00:00:03", 6),
    HostSpec("h_target", "target", "10.0.0.100/24", "00:00:00:00:00:64", 7),
)


@dataclass(frozen=True)
class SwitchLaunchConfig:
    """Validated inputs for launching the M1 BMv2 topology."""

    p4_json: Path
    link_mbps: int = 20
    thrift_port: int = 9090
    device_id: int = 0
    iperf_duration_s: int = 2
    switch_log: Path = Path("build/simple_switch.log")
    simple_switch_path: Path = Path("simple_switch")
    cli_path: Path = Path("simple_switch_CLI")

    def __post_init__(self) -> None:
        if not self.p4_json.is_file():
            raise FileNotFoundError(self.p4_json)
        topology_manifest(self.link_mbps)
        if self.thrift_port <= 0 or self.thrift_port > 65_535:
            raise ValueError("thrift_port must be between 1 and 65535")
        if self.device_id < 0:
            raise ValueError("device_id must be non-negative")
        if self.iperf_duration_s <= 0:
            raise ValueError("iperf_duration_s must be positive")


@dataclass(frozen=True)
class SmokeResult:
    """Verified M1 connectivity evidence returned by the launcher."""

    packet_loss_pct: float
    bits_per_second: float


class TopologySession(AbstractContextManager[Any]):
    """Own one programmed Mininet/BMv2 topology lifecycle."""

    def __init__(self, config: SwitchLaunchConfig) -> None:
        self._config = config
        self._network: Any | None = None

    def __enter__(self) -> Any:
        _validate_linux_runtime()
        network: Any | None = None
        try:
            network = _create_network(self._config)
            self._network = network
            network.build()
            network.start()
            SwitchApi(self._config.cli_path, self._config.thrift_port).execute(
                forwarding_commands()
            )
            return network
        except Exception:
            if network is not None:
                network.stop()
            self._network = None
            raise

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        del exc_type, exc_value, traceback
        network = self._network
        self._network = None
        if network is not None:
            network.stop()


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


def forwarding_commands() -> tuple[str, ...]:
    """Return deterministic BMv2 CLI commands for flooding and known unicast.

    Returns:
        Commands that configure the M1 multicast group and L2 forwarding table.
    """
    commands = [
        f"mc_mgrp_create {FLOOD_GROUP}",
        f"mc_node_create 0 {' '.join(str(host.switch_port) for host in HOSTS)}",
        f"mc_node_associate {FLOOD_GROUP} 0",
    ]
    commands.extend(
        f"table_add tbl_l2_forward set_egress_port {host.mac_address} => {host.switch_port}"
        for host in HOSTS
    )
    return tuple(commands)


def run_smoke(config: SwitchLaunchConfig) -> SmokeResult:
    """Launch `choke_v1`, install forwarding, and verify ping plus iperf.

    Args:
        config: Validated Linux/BMv2 launch inputs.

    Returns:
        Packet-loss and iperf throughput evidence.

    Raises:
        RuntimeError: If the Linux runtime or a connectivity check fails.
    """
    with TopologySession(config) as network:
        packet_loss = float(network.pingAll(timeout="2"))
        if packet_loss != 0.0:
            raise RuntimeError(f"M1 pingall failed with {packet_loss:.1f}% packet loss")

        target = network.get(TARGET_NAME)
        human = network.get(HUMAN_NAME)
        target.cmd("iperf3 -s -1 -D")
        time.sleep(0.2)
        target_ip = next(host.ip_address for host in HOSTS if host.name == TARGET_NAME).split("/")[
            0
        ]
        raw_result = human.cmd(f"iperf3 -c {target_ip} -t {config.iperf_duration_s} -J")
        bits_per_second = _parse_iperf_throughput(raw_result)
        LOGGER.info("M1 smoke passed: %.0f bits/s", bits_per_second)
        return SmokeResult(packet_loss_pct=packet_loss, bits_per_second=bits_per_second)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the M1 topology smoke check from the command line.

    Args:
        argv: Optional command-line arguments.

    Returns:
        Zero when the smoke check succeeds.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--p4-json", type=Path, default=Path("build/l2fwd.json"))
    parser.add_argument("--link-mbps", type=int, default=20)
    parser.add_argument("--thrift-port", type=int, default=9090)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        result = run_smoke(
            SwitchLaunchConfig(
                p4_json=args.p4_json,
                link_mbps=args.link_mbps,
                thrift_port=args.thrift_port,
            )
        )
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        LOGGER.error("M1 smoke failed: %s", exc)
        return 1
    LOGGER.info(
        "M1 result: packet_loss=%.1f%% throughput=%.0f bits/s",
        result.packet_loss_pct,
        result.bits_per_second,
    )
    return 0


def _validate_linux_runtime() -> None:
    if platform.system() != "Linux":
        raise RuntimeError("the Mininet/BMv2 topology requires Linux")
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        raise RuntimeError("the Mininet/BMv2 topology must run as root")
    required = ("simple_switch", "simple_switch_CLI", "iperf3")
    missing = [command for command in required if shutil.which(command) is None]
    if missing:
        raise RuntimeError(f"missing required Linux commands: {', '.join(missing)}")


def _create_network(config: SwitchLaunchConfig) -> Any:
    try:
        from mininet.link import TCLink
        from mininet.net import Mininet
        from mininet.node import Switch
    except ImportError as exc:
        raise RuntimeError(
            "Mininet is required; run this inside the Linux VM from docs/ENVIRONMENT.md"
        ) from exc

    class Bmv2Switch(Switch):
        """Mininet switch backed by one `simple_switch` process."""

        def start(self, controllers: list[object]) -> None:
            del controllers
            interface_args: list[str] = []
            for interface in self.intfList():
                port = self.ports.get(interface)
                if port is None or port <= 0:
                    continue
                interface_args.extend(("-i", f"{port}@{interface.name}"))

            config.switch_log.parent.mkdir(parents=True, exist_ok=True)
            self._log_handle = config.switch_log.open("w", encoding="utf-8")
            command = [
                str(config.simple_switch_path),
                "--device-id",
                str(config.device_id),
                "--thrift-port",
                str(config.thrift_port),
                *interface_args,
                str(config.p4_json),
            ]
            try:
                self._process = subprocess.Popen(
                    command,
                    stdout=self._log_handle,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
            except OSError:
                self._log_handle.close()
                raise
            try:
                _wait_for_switch_ready(
                    SwitchApi(config.cli_path, config.thrift_port),
                    self._process,
                )
            except RuntimeError:
                self._log_handle.close()
                raise

        def stop(self, deleteIntfs: bool = True) -> None:  # noqa: N803
            process = getattr(self, "_process", None)
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            log_handle = getattr(self, "_log_handle", None)
            if log_handle is not None and not log_handle.closed:
                log_handle.close()
            super().stop(deleteIntfs)

    return Mininet(
        topo=build_topology(config.link_mbps),
        switch=Bmv2Switch,
        controller=None,
        link=TCLink,
        autoSetMacs=False,
        autoStaticArp=False,
        build=False,
    )


def _wait_for_switch_ready(
    api: _RegisterReader,
    process: _ProcessProbe,
    *,
    timeout_s: float = 5.0,
    poll_interval_s: float = 0.05,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> None:
    deadline = clock() + timeout_s
    while True:
        if process.poll() is not None:
            raise RuntimeError("simple_switch exited during startup")
        try:
            api.read_register(POLICY_VERSION_REGISTER, index=0)
            return
        except SwitchApiError:
            if clock() >= deadline:
                raise RuntimeError(
                    f"simple_switch thrift API was not ready after {timeout_s:g} seconds"
                ) from None
            sleeper(poll_interval_s)


def _parse_iperf_throughput(raw_result: str) -> float:
    try:
        parsed = json.loads(raw_result)
        bits_per_second = float(parsed["end"]["sum_received"]["bits_per_second"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("iperf3 returned an unreadable result") from exc
    if bits_per_second <= 0:
        raise RuntimeError("iperf3 reported no received traffic")
    return bits_per_second


if __name__ == "__main__":
    raise SystemExit(main())
