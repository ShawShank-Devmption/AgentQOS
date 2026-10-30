"""Owned Linux Mininet/BMv2 launcher for the existing M1 l2fwd program."""

from __future__ import annotations

import argparse
import fcntl
import importlib.util
import json
import logging
import os
import platform
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common.contracts import (
    CLASS_TREATMENTS,
    CONGESTION_HIGH_PACKETS,
    CPU_CLONE_SESSION,
    CPU_CONTROLLER_INTERFACE,
    CPU_PORT,
    CPU_SWITCH_INTERFACE,
    POLICY_VERSION_REGISTER,
    PUNT_FILTER_TABLE,
    TARGET_SWITCH_PORT,
)
from controller.switch_api import SwitchApi, SwitchApiError
from harness.topology import HOSTS, build_topology, topology_manifest

logger = logging.getLogger(__name__)


class RuntimePrerequisiteError(RuntimeError):
    """The requested network cannot be launched in this environment."""


@dataclass(frozen=True)
class RuntimeConfig:
    """Explicit M1 runtime inputs; output directories are never reused."""

    pipeline_json: Path
    output_dir: Path
    link_mbps: int = 20
    thrift_port: int = 9090
    duration_s: int = 2
    queue_rate_pps: int | None = None

    def __post_init__(self) -> None:
        topology_manifest(self.link_mbps)
        if not 1 <= self.thrift_port <= 65_535:
            raise ValueError("thrift_port must be between 1 and 65535")
        if not 1 <= self.duration_s <= 120:
            raise ValueError("duration_s must be between 1 and 120")
        if self.queue_rate_pps is not None and (
            type(self.queue_rate_pps) is not int or self.queue_rate_pps <= 0
        ):
            raise ValueError("queue_rate_pps must be a positive integer when specified")


def cpu_port_required(pipeline_json: Path) -> bool:
    """Identify the agent pipeline by its frozen punt table in compiled BMv2 JSON."""
    try:
        artifact = json.loads(pipeline_json.read_text(encoding="utf-8"))
        return any(
            table["name"].rsplit(".", 1)[-1] == PUNT_FILTER_TABLE
            for pipeline in artifact["pipelines"]
            for table in pipeline["tables"]
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise RuntimePrerequisiteError("expected readable compiled BMv2 pipeline tables") from exc


def create_cpu_pair() -> None:
    """Create and raise the owned Linux veth pair joining BMv2 to clone reception."""
    subprocess.run(
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
        check=True,
    )
    try:
        for interface in (CPU_SWITCH_INTERFACE, CPU_CONTROLLER_INTERFACE):
            subprocess.run(["ip", "link", "set", "dev", interface, "up"], check=True)
    except subprocess.CalledProcessError:
        # The add succeeded: remove both ends if either link-up command fails.
        try:
            delete_cpu_pair()
        except subprocess.CalledProcessError:
            logger.exception("failed to remove CPU pair after link-up failure")
        raise


def delete_cpu_pair() -> None:
    """Delete the pair through its owned switch end; Linux removes the peer too."""
    subprocess.run(["ip", "link", "delete", CPU_SWITCH_INTERFACE], check=True)


def install_cpu_mirror(api: SwitchApi) -> None:
    """Point the frozen P4 clone session at the switch's dedicated CPU port."""
    api.execute([f"mirroring_add {CPU_CLONE_SESSION} {CPU_PORT}"])


def validate_prerequisites(config: RuntimeConfig) -> None:
    """Fail before mutating the network if Linux, tools, root or JSON are missing."""
    if platform.system() != "Linux":
        raise RuntimePrerequisiteError("Mininet/BMv2 requires Linux; see docs/ENVIRONMENT.md")
    if os.geteuid() != 0:
        raise RuntimePrerequisiteError("Mininet requires root; run the smoke command with sudo")
    missing = [
        name
        for name in ("simple_switch", "simple_switch_CLI", "iperf3", "ip", "tc", "ethtool")
        if shutil.which(name) is None
    ]
    if missing:
        raise RuntimePrerequisiteError(f"missing executable prerequisites: {', '.join(missing)}")
    if importlib.util.find_spec("mininet") is None:
        raise RuntimePrerequisiteError("Mininet Python package is unavailable in this interpreter")
    try:
        artifact = json.loads(config.pipeline_json.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimePrerequisiteError(f"unreadable BMv2 JSON: {config.pipeline_json}") from exc
    if not isinstance(artifact, dict) or "pipelines" not in artifact:
        raise RuntimePrerequisiteError("pipeline_json must be a compiled BMv2 program")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("0.0.0.0", config.thrift_port))
    except OSError as exc:
        raise RuntimePrerequisiteError(f"Thrift port {config.thrift_port} is unavailable") from exc
    for host in HOSTS:
        interface = Path("/sys/class/net") / f"s1-eth{host.switch_port}"
        if interface.exists():
            raise RuntimePrerequisiteError(f"topology interface already exists: {interface.name}")
    if cpu_port_required(config.pipeline_json):
        for interface_name in (CPU_SWITCH_INTERFACE, CPU_CONTROLLER_INTERFACE):
            if (Path("/sys/class/net") / interface_name).exists():
                raise RuntimePrerequisiteError(f"CPU interface already exists: {interface_name}")


def stop_process(child: subprocess.Popen[Any], timeout_s: float = 3.0) -> None:
    """Terminate and reap an owned child, escalating only that child if needed."""
    if child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=timeout_s)
    else:
        child.wait(timeout=timeout_s)


def switch_command(
    config: RuntimeConfig, interfaces: Sequence[tuple[int, str]], cpu_enabled: bool
) -> list[str]:
    """Build BMv2 argv with enough queues for every frozen class treatment."""
    command = ["simple_switch", "--device-id", "0", "--thrift-port", str(config.thrift_port)]
    for port, interface in sorted(interfaces):
        command.extend(["-i", f"{port}@{interface}"])
    if cpu_enabled:
        command.extend(["-i", f"{CPU_PORT}@{CPU_SWITCH_INTERFACE}"])
    queue_count = max(treatment.queue_priority for treatment in CLASS_TREATMENTS.values()) + 1
    command.extend(
        [str(config.pipeline_json.resolve()), "--", "--priority-queues", str(queue_count)]
    )
    return command


def configure_switch_queues(api: SwitchApi, config: RuntimeConfig) -> None:
    """Place the configured fixed-packet bottleneck in BMv2's priority queues."""
    if config.queue_rate_pps is None:
        return
    # §7.7: the queue can grow past HI before a dequeue observes it and sets protection.
    api.execute(
        [
            f"set_queue_depth {4 * CONGESTION_HIGH_PACKETS} {TARGET_SWITCH_PORT}",
            f"set_queue_rate {config.queue_rate_pps} {TARGET_SWITCH_PORT}",
        ]
    )


def disable_checksum_offload(host: Any) -> None:
    """Make a Mininet host compute its own TCP/UDP checksums before frames leave it.

    With veth TX offload on, the kernel leaves checksums for hardware to finish. BMv2
    forwards those bytes unchanged, so the receiver drops every TCP/UDP packet as corrupt.

    Raises:
        RuntimePrerequisiteError: If ethtool cannot change the host interface.
    """
    interface = host.defaultIntf().name
    _, error, code = host.pexec(
        ["ethtool", "--offload", interface, "rx", "off", "tx", "off", "sg", "off"]
    )
    if code != 0:
        raise RuntimePrerequisiteError(
            f"cannot disable checksum offload on {interface}: {error.strip()}"
        )


def create_network(config: RuntimeConfig) -> Any:
    """Construct a Mininet network using a custom BMv2 switch, never an OVS default."""
    cpu_enabled = cpu_port_required(config.pipeline_json)
    from mininet.link import TCLink
    from mininet.net import Mininet
    from mininet.node import Host, Switch

    class SoftwareChecksumHost(Host):
        """Mininet host whose frames carry complete checksums when they reach BMv2."""

        def config(self, **params: Any) -> Any:
            """Apply Mininet's address setup, then disable checksum offload."""
            result = super().config(**params)
            disable_checksum_offload(self)
            return result

    class Bmv2Switch(Switch):
        """Mininet-owned process for the M1 simple_switch instance."""

        def start(self, controllers: list[Any]) -> None:
            """Attach each topology port to the same explicit BMv2 device port."""
            del controllers
            self._bmv2 = None
            self._cpu_created = False
            self._log = (config.output_dir / "simple-switch.log").open("x", encoding="utf-8")
            interfaces = []
            for port, interface in sorted(self.intfs.items()):
                if port == 0:
                    continue
                self.cmd("ip", "link", "set", "dev", interface.name, "up")
                interfaces.append((port, interface.name))
            try:
                if cpu_enabled:
                    create_cpu_pair()
                    self._cpu_created = True
                command = switch_command(config, interfaces, cpu_enabled)
                self._bmv2 = self.popen(command, stdout=self._log, stderr=subprocess.STDOUT)
                if cpu_enabled:
                    api = SwitchApi(thrift_port=config.thrift_port)
                    wait_for_switch(api)
                    install_cpu_mirror(api)
            except Exception:
                if self._bmv2 is not None:
                    try:
                        stop_process(self._bmv2)
                    except Exception:
                        logger.exception("failed to reap BMv2 after CPU attachment failure")
                    self._bmv2 = None
                if self._cpu_created:
                    try:
                        delete_cpu_pair()
                    except subprocess.CalledProcessError:
                        logger.exception("failed to remove CPU pair after switch startup failure")
                    else:
                        self._cpu_created = False
                self._log.close()
                raise

        def stop(self, delete_intfs: bool = True) -> None:
            """Reap the BMv2 child and delete this switch's interfaces."""
            try:
                if getattr(self, "_bmv2", None) is not None:
                    stop_process(self._bmv2)
            finally:
                if getattr(self, "_log", None) is not None:
                    self._log.close()
                try:
                    if getattr(self, "_cpu_created", False):
                        delete_cpu_pair()
                        self._cpu_created = False
                finally:
                    super().stop(delete_intfs)

    return Mininet(
        topo=build_topology(config.link_mbps),
        host=SoftwareChecksumHost,
        switch=Bmv2Switch,
        controller=None,
        link=TCLink,
        autoSetMacs=False,
        build=False,
    )


def wait_for_switch(api: SwitchApi, timeout_s: float = 10.0) -> None:
    """Wait for the owned BMv2 Thrift endpoint to expose its live M1 register."""
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            api.read_register(POLICY_VERSION_REGISTER, 0)
            return
        except SwitchApiError:
            if time.monotonic() >= deadline:
                raise
            logger.debug("waiting for BMv2 Thrift startup", exc_info=True)
            time.sleep(0.1)


def install_l2_forwarding(api: SwitchApi) -> None:
    """Program the skeleton's existing table and PRE via the sole SwitchApi boundary."""
    # M1's tbl_l2_forward is defined by l2fwd.p4, outside the classifier table contracts.
    ports = " ".join(str(host.switch_port) for host in HOSTS)
    # A new, exclusively owned switch has no PRE nodes; its first node handle is zero.
    commands = ["mc_mgrp_create 1", f"mc_node_create 0 {ports}", "mc_node_associate 1 0"]
    commands.extend(
        f"table_add tbl_l2_forward set_egress_port {host.mac_address} => {host.switch_port}"
        for host in HOSTS
    )
    api.execute(commands)


def run_smoke(config: RuntimeConfig) -> dict[str, Any]:
    """Run one measured iperf flow and register read, always stopping owned resources."""
    # §7.19: only one choke_v1 network may own the fixed interface names at a time.
    lock_path = Path(tempfile.gettempdir()) / "agent-aware-choke-v1-runtime.lock"
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimePrerequisiteError("another choke_v1 runtime is active") from exc
        return _run_smoke_locked(config)


def _run_smoke_locked(config: RuntimeConfig) -> dict[str, Any]:
    validate_prerequisites(config)
    config.output_dir.mkdir(parents=True, exist_ok=False)
    network = None
    server = None
    client = None
    manifest: dict[str, Any] = {"status": "failed", "topology": topology_manifest(config.link_mbps)}
    try:
        network = create_network(config)
        network.build()
        network.start()
        api = SwitchApi(thrift_port=config.thrift_port)
        wait_for_switch(api)
        configure_switch_queues(api, config)
        install_l2_forwarding(api)
        for host in HOSTS:
            for peer in HOSTS:
                if host != peer:
                    network.get(host.name).setARP(peer.ip_address.split("/")[0], peer.mac_address)
        # §7.5: inspect the live version without resetting controller-owned policy state.
        (version,) = api.read_register(POLICY_VERSION_REGISTER, 0)
        target = next(host for host in HOSTS if host.role == "target")
        human = next(host for host in HOSTS if host.role == "human")
        server = network.get(target.name).popen(
            ["iperf3", "-s", "-1", "-J"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        # Poll the namespace's listening socket rather than racing server startup.
        _wait_for_iperf(network.get(target.name), server)
        client = network.get(human.name).popen(
            ["iperf3", "-c", target.ip_address.split("/")[0], "-t", str(config.duration_s), "-J"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout, stderr = client.communicate(timeout=config.duration_s + 10)
        (config.output_dir / "iperf-client.json").write_text(stdout, encoding="utf-8")
        (config.output_dir / "iperf-client.stderr").write_text(stderr, encoding="utf-8")
        if client.returncode != 0:
            raise RuntimeError(f"iperf client failed: {stderr.strip()}")
        measured = json.loads(stdout)
        if measured.get("error") or measured["end"]["sum_received"]["bits_per_second"] <= 0:
            raise RuntimeError(f"iperf did not measure positive throughput: {measured}")
        server_stdout, server_stderr = server.communicate(timeout=5)
        (config.output_dir / "iperf-server.json").write_text(server_stdout, encoding="utf-8")
        (config.output_dir / "iperf-server.stderr").write_text(server_stderr, encoding="utf-8")
        if server.returncode != 0:
            raise RuntimeError(f"iperf server failed: {server_stderr.strip()}")
        manifest.update(
            status="passed",
            policy_version=version,
            bits_per_second=measured["end"]["sum_received"]["bits_per_second"],
        )
        return manifest
    except BaseException as exc:
        manifest["error"] = f"{type(exc).__name__}: {exc}"
        logger.exception("M1 smoke failed")
        raise
    finally:
        cleanup_errors: list[Exception] = []
        for child in (client, server):
            if child is not None:
                try:
                    stop_process(child)
                except Exception as exc:
                    logger.exception("failed to reap owned workload process")
                    cleanup_errors.append(exc)
        if network is not None:
            try:
                network.stop()
            except Exception as exc:
                logger.exception("failed to stop owned Mininet network")
                cleanup_errors.append(exc)
        if cleanup_errors:
            manifest["status"] = "failed"
            manifest["cleanup_errors"] = [str(error) for error in cleanup_errors]
        (config.output_dir / "smoke.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        if cleanup_errors and "error" not in manifest:
            raise RuntimeError("M1 cleanup failed; inspect smoke.json") from cleanup_errors[0]


def _wait_for_iperf(host: Any, child: subprocess.Popen[Any]) -> None:
    for _ in range(50):
        if child.poll() is not None:
            raise RuntimeError("iperf server exited before listening")
        probe = host.popen(
            [
                sys.executable,
                "-c",
                "import socket; s=socket.socket(); s.bind(('0.0.0.0',5201)); s.close()",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # A failed bind indicates the server owns its port, without consuming its -1 session.
        if probe.wait(timeout=2) != 0:
            return
        time.sleep(0.1)
    raise RuntimeError("iperf server did not start listening")


def main() -> None:
    """Launch the explicitly requested Linux M1 smoke from the command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pipeline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--link-mbps", type=int, default=20)
    parser.add_argument("--thrift-port", type=int, default=9090)
    parser.add_argument("--duration", type=int, default=2)
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    previous_handler = signal.signal(signal.SIGTERM, _interrupt)
    try:
        result = run_smoke(
            RuntimeConfig(
                arguments.pipeline,
                arguments.output,
                arguments.link_mbps,
                arguments.thrift_port,
                arguments.duration,
            )
        )
        logger.info("M1 smoke passed: %s", result)
    finally:
        signal.signal(signal.SIGTERM, previous_handler)


def _interrupt(signum: int, frame: Any) -> None:
    del frame
    raise SystemExit(128 + signum)


if __name__ == "__main__":
    main()
