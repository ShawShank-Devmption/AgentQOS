"""P4.4 real scaling workload adapter for eval.run_experiment (Linux only)."""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from common.contracts import (
    COLLISION_COUNTER_REGISTER,
    EXPERIMENT_TOPOLOGY,
    FLOW_KEY_REGISTER,
    LAST_TIMESTAMP_REGISTER,
    PACKET_COUNT_REGISTER,
)
from controller.switch_api import SwitchApi
from eval.metrics import write_json
from eval.state_scaling import analyze, load_prepared
from eval.workload import _wait_for_ready, archive_inputs, network_context
from harness.runtime import RuntimeConfig, RuntimePrerequisiteError, stop_process
from harness.topology import HOSTS

logger = logging.getLogger(__name__)


def capture_command(interface: str, direction: str, output: Path) -> list[str]:
    """Capture only the specified path direction, retaining complete packet identities."""
    if direction not in {"in", "out"}:
        raise ValueError("invalid capture direction")
    return ["tcpdump", "-i", interface, "-Q", direction, "-n", "-U", "-w", str(output)]


def snapshot(api: SwitchApi) -> dict[str, list[int]]:
    """Reject a changing register snapshot rather than publish torn scaling state."""
    # §7.4: bracket reads of ownership/timestamps/collision count with packet counts and tags.
    names = [
        PACKET_COUNT_REGISTER,
        FLOW_KEY_REGISTER,
        COLLISION_COUNTER_REGISTER,
        LAST_TIMESTAMP_REGISTER,
        COLLISION_COUNTER_REGISTER,
        FLOW_KEY_REGISTER,
        PACKET_COUNT_REGISTER,
    ]
    counts1, keys1, collision1, timestamps, collision2, keys2, counts2 = api.read_registers_ordered(
        names
    )
    if counts1 != counts2 or keys1 != keys2 or collision1 != collision2:
        raise RuntimeError("switch state changed during snapshot; drain was insufficient")
    return {
        PACKET_COUNT_REGISTER: list(counts2),
        FLOW_KEY_REGISTER: list(keys2),
        COLLISION_COUNTER_REGISTER: list(collision2),
        LAST_TIMESTAMP_REGISTER: list(timestamps),
    }


def _capture_ready(path: Path, child: subprocess.Popen[Any]) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise RuntimeError(f"capture exited early; inspect {path}")
        if path.exists() and "listening on" in path.read_text():
            return
        time.sleep(0.05)
    raise TimeoutError(f"tcpdump did not report readiness: {path}")


def read_rss_bytes(pid: int, proc_root: Path = Path("/proc")) -> int:
    """Read actual switch resident memory separately from declared logical register bits."""
    for line in (proc_root / str(pid) / "status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            words = line.split()
            if len(words) == 3 and words[2] == "kB" and int(words[1]) >= 0:
                return int(words[1]) * 1024
    raise ValueError("switch VmRSS is unavailable")


def main() -> int:
    """Replay a pre-generated scaling cell and archive actual captures/register evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--flows", type=int, required=True)
    parser.add_argument("--switch-cpu", type=int, required=True)
    args = parser.parse_args()
    if os.environ["AAN_SYSTEM"] != "ours" or os.environ["AAN_TOPOLOGY"] != EXPERIMENT_TOPOLOGY:
        raise ValueError("state scaling requires ours on choke_v1")
    for tool in ("tcpdump", "tcpreplay"):
        if shutil.which(tool) is None:
            raise RuntimePrerequisiteError(f"scaling requires {tool}")
    seed = int(os.environ["AAN_SEED"])
    source = args.inputs / f"seed-{seed}" / f"flows-{args.flows}"
    metadata = load_prepared(source)
    if metadata["flow_count"] != args.flows or metadata["seed"] != seed:
        raise ValueError("prepared flow count/seed differs from the requested cell")
    if metadata["agent_share_pct"] != int(os.environ["AAN_AGENT_SHARE_PCT"]):
        raise ValueError("prepared traffic share differs from the requested cell")
    if os.environ["AAN_BURST_INTENSITY"] != "low":
        raise ValueError("scaling traces implement low burst only")
    profile = metadata["profile"]
    duration = int(os.environ["AAN_DURATION_S"])
    link = int(os.environ["AAN_LINK_MBPS"])
    replay_s = args.flows * profile["packets_per_flow"] / profile["offered_pps"]
    if replay_s + profile["drain_s"] >= duration:
        raise ValueError("configured duration cannot contain the replay and drain")
    if profile["offered_pps"] * (42 + profile["payload_bytes"]) * 8 > link * 1e6:
        raise ValueError("configured offered traffic exceeds link capacity")
    output = Path(os.environ["AAN_OUTPUT_DIR"])
    prepared = output / "prepared"
    shutil.copytree(source, prepared)
    load_prepared(prepared)
    pipeline = Path("build/agent_aware.json")
    archive_inputs(
        output,
        {
            "profile": prepared / "profile.json",
            "pipeline": pipeline,
            "model": Path(os.environ["AAN_COMPILED_TREE"]),
        },
    )
    runtime = RuntimeConfig(
        pipeline,
        output / "network",
        link_mbps=link,
        queue_rate_pps=link * 1_000_000 // (8 * (42 + profile["payload_bytes"])),
        switch_cpu=args.switch_cpu,
    )
    sender = next(host for host in HOSTS if host.role == "agent")
    target = next(host for host in HOSTS if host.role == "target")
    ingress = output / "ingress/traffic.pcap"
    egress = output / "egress.pcap"
    ingress.parent.mkdir()
    children = []
    logs = []
    try:
        with network_context("ours", runtime, {}, {}) as network:
            switch = network.get("s1")._bmv2
            if os.sched_getaffinity(switch.pid) != {args.switch_cpu}:
                raise RuntimeError("scaling switch affinity differs from the requested CPU")
            api = SwitchApi(thrift_port=runtime.thrift_port)
            before = list(api.read_register(COLLISION_COUNTER_REGISTER, 0))
            if before != [0]:
                raise RuntimeError("state scaling requires a fresh switch collision counter")
            # A bound UDP sink suppresses kernel ICMP replies to our synthetic source addresses.
            ready = output / "sink-ready.json"
            sink_code = (
                "import json,socket,sys,time; from pathlib import Path; "
                "s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); "
                "s.bind(('0.0.0.0',int(sys.argv[1]))); s.settimeout(1); "
                "Path(sys.argv[2]).write_text(json.dumps({'ready':True})); "
                "time.sleep(float(sys.argv[3]))"
            )
            sink = network.get(target.name).popen(
                [
                    sys.executable,
                    "-c",
                    sink_code,
                    str(profile["destination_port"]),
                    str(ready),
                    str(duration + 30),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            children.append(sink)
            _wait_for_ready(ready, sink)
            for interface, direction, path, name in (
                (f"s1-eth{sender.switch_port}", "in", ingress, "ingress"),
                (f"s1-eth{target.switch_port}", "out", egress, "egress"),
            ):
                log_path = output / f"tcpdump-{name}.log"
                log = log_path.open("x")
                logs.append(log)
                child = subprocess.Popen(
                    capture_command(interface, direction, path),
                    stdout=subprocess.DEVNULL,
                    stderr=log,
                )
                children.append(child)
                _capture_ready(log_path, child)
            start = time.time()
            with (output / "replay.log").open("x") as log:
                replay = network.get(sender.name).popen(
                    [
                        "tcpreplay",
                        "--intf1",
                        network.get(sender.name).defaultIntf().name,
                        "--multiplier",
                        "1.0",
                        str((prepared / "traffic.pcap").resolve()),
                    ],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
                children.append(replay)
                if replay.wait(timeout=duration) != 0:
                    raise RuntimeError("scaling replay failed; inspect replay.log")
            time.sleep(profile["drain_s"])
            end = time.time()
            for child in children[1:3]:
                if child.poll() is not None:
                    raise RuntimeError("tcpdump exited during scaling workload")
                stop_process(child)
            registers = {
                "before": {COLLISION_COUNTER_REGISTER: before},
                "after": snapshot(api),
                "switch_rss_bytes": read_rss_bytes(switch.pid),
                "conditions": {
                    "switch_cpu": args.switch_cpu,
                    "switch_binary_sha256": hashlib.sha256(
                        Path(shutil.which("simple_switch")).read_bytes()
                    ).hexdigest(),
                },
            }
            write_json(output / "register-snapshot.json", registers)
            write_json(output / "capture-window.json", {"start_s": start, "end_s": end})
    finally:
        interrupted = sys.exc_info()[0] is not None
        cleanup_errors = []
        for child in reversed(children):
            try:
                stop_process(child)
            except Exception as exc:
                logger.exception("failed to stop an owned scaling actor")
                cleanup_errors.append(exc)
        for log in logs:
            log.close()
        if cleanup_errors and not interrupted:
            raise RuntimeError("scaling cleanup failed") from cleanup_errors[0]
    analyze(
        prepared,
        ingress,
        egress,
        output / "register-snapshot.json",
        output / "inputs/pipeline.json",
        start,
        end,
        output / "analysis",
    )
    shutil.copyfile(output / "analysis/observations.json", os.environ["AAN_OBSERVATIONS_PATH"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
