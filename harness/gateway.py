"""G2 inline switch lifecycle; I3 owns interfaces, aggregate shaping, and bypass."""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import re
import shlex
import shutil
import signal
import socket
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common.contracts import (
    CONGESTION_FLAG_REGISTER,
    CONGESTION_HIGH_PACKETS,
    CPU_CLONE_SESSION,
    CPU_CONTROLLER_INTERFACE,
    CPU_PORT,
    FLOOD_GROUP,
    GATEWAY_CPU_INTERFACE,
    GATEWAY_HEALTH_POLL_S,
    GATEWAY_LAN_INTERFACE,
    GATEWAY_LAN_PORT,
    GATEWAY_MAX_FRAME_BYTES,
    GATEWAY_PORTS,
    GATEWAY_TOPOLOGY,
    GATEWAY_WAN_INTERFACE,
    GATEWAY_WAN_PORT,
    TABLE_NAMES,
)
from controller.switch_api import SwitchApi
from eval.metrics import write_json
from eval.run_experiment import ExperimentConfig, host_lock, load_config
from harness.runtime import (
    RuntimeConfig,
    RuntimePrerequisiteError,
    create_cpu_pair,
    delete_cpu_pair,
    stop_process,
    switch_command,
    switch_logging_supported,
    wait_for_switch,
)
from harness.topology import gateway_manifest

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GatewayConfig:
    """Explicit deployment inputs; a launcher session is not a measured experiment."""

    experiment: ExperimentConfig
    output: Path
    scripts: Path = Path("harness/gateway")
    thrift_port: int = 9090
    switch_cpu: int | None = None

    def __post_init__(self) -> None:
        if self.experiment.topology != GATEWAY_TOPOLOGY:
            raise ValueError("gateway requires the live_gateway topology")
        if self.experiment.system not in {"fifo", "ours"}:
            raise ValueError("gateway supports fifo and ours only")
        RuntimeConfig(
            self.pipeline,
            self.output,
            self.experiment.link_mbps,
            thrift_port=self.thrift_port,
            switch_cpu=self.switch_cpu,
        )

    @property
    def pipeline(self) -> Path:
        """Select a separate artifact whose egress suppresses reflected flood replicas."""
        return Path(
            "build/agent_gateway.json"
            if self.experiment.system == "ours"
            else "build/fifo_gateway.json"
        )


def gateway_command(config: GatewayConfig, *, logging_supported: bool = True) -> list[str]:
    """Attach only the approved LAN/WAN and optional CPU interfaces."""
    interfaces = [
        (GATEWAY_LAN_PORT, GATEWAY_LAN_INTERFACE),
        (GATEWAY_WAN_PORT, GATEWAY_WAN_INTERFACE),
    ]
    if config.experiment.system == "ours":
        interfaces.append((CPU_PORT, GATEWAY_CPU_INTERFACE))
    runtime = RuntimeConfig(
        config.pipeline,
        config.output,
        config.experiment.link_mbps,
        thrift_port=config.thrift_port,
        switch_cpu=config.switch_cpu,
    )
    return switch_command(
        runtime, interfaces, cpu_enabled=False, logging_supported=logging_supported
    )


def gateway_policy_commands(link_mbps: int, lan_mac: str, wan_mac: str) -> list[str]:
    """Install peer forwarding, a CPU-free flood group, and bidirectional queue settings."""
    for mac in (lan_mac, wan_mac):
        if re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", mac) is None:
            raise ValueError("invalid gateway peer MAC")
        if int(mac[:2], 16) & 1 or int(mac.replace(":", ""), 16) == 0:
            raise ValueError("gateway peer MAC must be nonzero unicast")
    if lan_mac.lower() == wan_mac.lower():
        raise ValueError("gateway peer MACs must be distinct")
    manifest = gateway_manifest(link_mbps)
    commands = [
        f"mc_mgrp_create {FLOOD_GROUP}",
        f"mc_node_create 0 {' '.join(str(port) for port in GATEWAY_PORTS)}",
        f"mc_node_associate {FLOOD_GROUP} 0",
        f"table_add tbl_l2_forward set_egress_port {lan_mac} => {GATEWAY_LAN_PORT}",
        f"table_add tbl_l2_forward set_egress_port {wan_mac} => {GATEWAY_WAN_PORT}",
    ]
    for port in GATEWAY_PORTS:
        commands.extend(
            [
                f"set_queue_depth {manifest['queue_depth_packets']} {port}",
                f"set_queue_rate {manifest['queue_rate_pps']} {port}",
            ]
        )
    return commands


def validate_gateway_pipeline(path: Path, system: str) -> None:
    """Require gateway congestion ABI and compiled ingress-reflection exclusion."""
    if system not in {"ours", "fifo"}:
        raise ValueError("gateway supports fifo and ours only")
    artifact = json.loads(path.read_text())
    tables = {
        table["name"].rsplit(".", 1)[-1]
        for pipe in artifact["pipelines"]
        for table in pipe["tables"]
    }
    if system == "ours" and not set(TABLE_NAMES) <= tables:
        raise RuntimePrerequisiteError("gateway artifact lacks classifier/QoS tables")
    if system == "fifo" and any(name in tables for name in TABLE_NAMES):
        raise RuntimePrerequisiteError("FIFO gateway cannot contain classifier/QoS tables")
    registers = {reg["name"].rsplit(".", 1)[-1]: reg for reg in artifact["register_arrays"]}
    if system == "ours" and registers.get(CONGESTION_FLAG_REGISTER, {}).get("size") != 2:
        raise RuntimePrerequisiteError("gateway requires separate LAN/WAN congestion flags")
    # Reject accidentally selected original artifacts before the bypass is disconnected.
    egress = [json.dumps(pipe) for pipe in artifact["pipelines"] if pipe["name"] == "egress"]
    if not any("ingress_port" in pipe and "egress_port" in pipe for pipe in egress):
        raise RuntimePrerequisiteError("gateway artifact lacks egress reflection guard")


def aggregate_shaping_commands(link_mbps: int) -> list[list[str]]:
    """Cap aggregate egress on both I3 links using the configured link rate."""
    manifest = gateway_manifest(link_mbps)
    return [
        [
            "tc",
            "qdisc",
            "replace",
            "dev",
            spec["interface"],
            "root",
            "tbf",
            "rate",
            f"{link_mbps}mbit",
            "burst",
            str(CONGESTION_HIGH_PACKETS * GATEWAY_MAX_FRAME_BYTES),
            "limit",
            str(manifest["queue_depth_packets"] * GATEWAY_MAX_FRAME_BYTES),
        ]
        for spec in manifest["ports"]
    ]


def infrastructure_namespace(settings: Path) -> str:
    """Read I3's namespace literal without executing a sourced shell configuration."""
    values = []
    for line in settings.read_text().splitlines():
        if line.startswith("AAN_NETNS="):
            words = shlex.split(line.split("=", 1)[1], comments=True)
            if len(words) != 1 or re.fullmatch(r"[a-zA-Z0-9_.-]+", words[0]) is None:
                raise RuntimePrerequisiteError("I3 AAN_NETNS must be a literal namespace name")
            values.append(words[0])
    if len(values) != 1:
        raise RuntimePrerequisiteError("I3 settings must define exactly one AAN_NETNS")
    return values[0]


def _link(name: str, namespace: str | None = None) -> dict[str, Any]:
    command = ["ip", "-json"]
    if namespace is not None:
        command.extend(["-n", namespace])
    command.extend(["link", "show", "dev", name])
    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    rows = json.loads(result.stdout)
    if len(rows) != 1 or rows[0].get("mtu", 0) != GATEWAY_MAX_FRAME_BYTES - 14:
        raise RuntimePrerequisiteError(f"gateway requires MTU 1500 on {name}")
    return rows[0]


def validate_prerequisites(config: GatewayConfig) -> tuple[str, str]:
    """Reject missing I3 handover/shaping, tools, or compiled artifacts before takeover."""
    if platform.system() != "Linux" or os.geteuid() != 0:
        raise RuntimePrerequisiteError("inline gateway requires Linux and root")
    for name in ("simple_switch", "simple_switch_CLI", "ip", "tc"):
        if shutil.which(name) is None:
            raise RuntimePrerequisiteError(f"gateway missing executable: {name}")
    if config.switch_cpu is not None and (
        shutil.which("taskset") is None or config.switch_cpu not in os.sched_getaffinity(0)
    ):
        raise RuntimePrerequisiteError("gateway switch_cpu requires taskset and an available CPU")
    for name in ("netsetup.sh", "bypass.sh"):
        path = config.scripts / name
        if not path.is_file() or not os.access(path, os.X_OK):
            raise RuntimePrerequisiteError(f"I3 must provide executable {path}")
    # Never attach policy commands to an unrelated switch already owning this endpoint.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("0.0.0.0", config.thrift_port))
    except OSError as exc:
        raise RuntimePrerequisiteError("gateway Thrift port is already in use") from exc
    validate_gateway_pipeline(config.pipeline, config.experiment.system)
    for interface in (GATEWAY_LAN_INTERFACE, GATEWAY_WAN_INTERFACE):
        _link(interface)
        result = subprocess.run(
            ["tc", "-json", "qdisc", "show", "dev", interface],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if any(
            row.get("root") and row.get("kind") not in {"tbf", "htb", "noqueue"}
            for row in json.loads(result.stdout)
        ):
            raise RuntimePrerequisiteError(f"unexpected existing qdisc on I3 link {interface}")
    if config.experiment.system == "ours":
        for name in (GATEWAY_CPU_INTERFACE, CPU_CONTROLLER_INTERFACE):
            if (Path("/sys/class/net") / name).exists():
                raise RuntimePrerequisiteError(f"gateway CPU interface already exists: {name}")
    namespace = infrastructure_namespace(config.scripts / "gateway.env")
    lan_mac = _link("gw-lan", namespace)["address"]
    wan_mac = _link("gw-wan")["address"]
    gateway_policy_commands(config.experiment.link_mbps, lan_mac, wan_mac)
    return lan_mac, wan_mac


@contextmanager
def running_gateway(config: GatewayConfig) -> Iterator[subprocess.Popen[Any]]:
    """Take over I3's gateway and restore bypass after stopping the owned switch."""
    # §7.19: share the experiment lock; never take over during a benchmark or another gateway.
    with host_lock():
        lan_mac, wan_mac = validate_prerequisites(config)
        config.output.mkdir(parents=True, exist_ok=False)
        child = None
        cpu_owned = False
        failed = False
        try:
            for command in aggregate_shaping_commands(config.experiment.link_mbps):
                subprocess.run(command, check=True, timeout=5)
            # I3 owns this handover: no link recreation and tc stays installed.
            subprocess.run(
                [str((config.scripts / "netsetup.sh").resolve()), "--switch"],
                check=True,
                timeout=10,
            )
            if config.experiment.system == "ours":
                create_cpu_pair(GATEWAY_CPU_INTERFACE, CPU_CONTROLLER_INTERFACE)
                cpu_owned = True
            with (config.output / "simple-switch.log").open("x") as log:
                child = subprocess.Popen(
                    gateway_command(config, logging_supported=switch_logging_supported()),
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
                api = SwitchApi(thrift_port=config.thrift_port)
                wait_for_switch(api)
                if child.poll() is not None:
                    raise RuntimeError("gateway switch exited during startup")
                api.execute(gateway_policy_commands(config.experiment.link_mbps, lan_mac, wan_mac))
                if config.experiment.system == "ours":
                    api.execute([f"mirroring_add {CPU_CLONE_SESSION} {CPU_PORT}"])
                write_json(
                    config.output / "gateway-started.json",
                    {
                        "system": config.experiment.system,
                        "argv": child.args,
                        "link_mbps": config.experiment.link_mbps,
                        "lan_mac": lan_mac,
                        "wan_mac": wan_mac,
                        "topology": gateway_manifest(config.experiment.link_mbps),
                        "note": "Deployment lifecycle, not performance evidence",
                    },
                )
                yield child
        except BaseException:
            failed = True
            logger.exception("gateway session failed; stopping switch and restoring bypass")
            raise
        finally:
            try:
                if child is not None:
                    stop_process(child)
                # Do not enable the bridge while BMv2 is still forwarding.
                try:
                    if cpu_owned:
                        delete_cpu_pair(GATEWAY_CPU_INTERFACE)
                finally:
                    subprocess.run(
                        [str((config.scripts / "bypass.sh").resolve())], check=True, timeout=10
                    )
            except Exception:
                logger.exception("gateway cleanup failed; immediate I3 operator attention required")
                if not failed:
                    raise


def monitor_gateway(child: subprocess.Popen[Any], duration_s: int) -> None:
    """Detect switch exit within the approved health polling interval."""
    deadline = time.monotonic() + duration_s
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise RuntimeError(f"gateway switch exited with status {child.returncode}")
        time.sleep(min(GATEWAY_HEALTH_POLL_S, max(0, deadline - time.monotonic())))


def _interrupt(signum: int, frame: Any) -> None:
    del frame
    raise SystemExit(128 + signum)


def main() -> None:
    """Deploy one config-selected gateway session; capture measurements separately."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--scripts", type=Path, default=Path("harness/gateway"))
    parser.add_argument("--thrift-port", type=int, default=9090)
    parser.add_argument("--switch-cpu", type=int)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    config = GatewayConfig(
        load_config(args.config), args.output, args.scripts, args.thrift_port, args.switch_cpu
    )
    previous = signal.signal(signal.SIGTERM, _interrupt)
    try:
        with running_gateway(config) as child:
            monitor_gateway(child, config.experiment.duration_s)
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    main()
