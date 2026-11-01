"""C4/P4.3 fixed-pps workload, invoked only through eval.run_experiment."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any

from common.contracts import (
    CPU_SWITCH_INTERFACE,
    EXPERIMENT_TOPOLOGY,
    PUNT_BURST_PACKETS,
    PUNT_RATE_PPS,
)
from eval.metrics import write_json
from eval.workload import (
    _run_network_traffic,
    archive_inputs,
    build_plan,
    load_profile,
    network_context,
    queue_rate_pps,
)
from harness.runtime import RuntimeConfig

logger = logging.getLogger(__name__)


def build_overhead_plan(
    profile: dict[str, Any],
    *,
    pps: int,
    seed: int,
    share: int,
    burst: str,
    duration: int,
    link_mbps: int,
) -> dict[str, Any]:
    """Reuse the seeded workload scheduler with a specified aggregate offered packet rate."""
    if type(pps) is not int or pps <= 0:
        raise ValueError("pps must be a positive integer")
    fixed = dict(profile)
    fixed["offered_load_multiplier"] = pps * profile["payload_bytes"] * 8 / (link_mbps * 1e6)
    return build_plan(
        fixed, seed=seed, share=share, burst=burst, duration=duration, link_mbps=link_mbps
    )


def read_cpu_ticks(pid: int, proc_root: Path = Path("/proc")) -> int:
    """Read process-wide user+system CPU ticks, including all BMv2 threads."""
    raw = (proc_root / str(pid) / "stat").read_text(encoding="utf-8")
    try:
        # comm may contain whitespace and parentheses; fields after its final ')' start at 3.
        fields = raw[raw.rindex(")") + 1 :].split()
        return int(fields[11]) + int(fields[12])
    except (ValueError, IndexError) as exc:
        raise ValueError(f"invalid process stat for pid {pid}") from exc


def read_punt_packets(cpu_enabled: bool, net_root: Path = Path("/sys/class/net")) -> int | None:
    """Read clones emitted on the owned CPU veth; FIFO has no CPU port (unavailable)."""
    if not cpu_enabled:
        return None
    return int((net_root / CPU_SWITCH_INTERFACE / "statistics/tx_packets").read_text())


def pipeline_footprint(path: Path) -> dict[str, Any]:
    """Count declared table capacities and logical register storage from compiled JSON."""
    artifact = json.loads(path.read_text(encoding="utf-8"))
    registers = artifact["register_arrays"]
    return {
        "table_capacity_entries": sum(
            table["max_size"] for pipe in artifact["pipelines"] for table in pipe["tables"]
        ),
        "register_bits": sum(reg["size"] * reg["bitwidth"] for reg in registers),
        "note": "Declared capacity and logical bits; not BMv2 resident memory or installed entries",
    }


def main() -> int:
    """Measure one real fixed-rate cell under the existing locked experiment runner."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pps", type=int, required=True)
    parser.add_argument("--switch-cpu", type=int, required=True)
    parser.add_argument("--profile", type=Path, default=Path("eval/configs/workload_runtime.json"))
    args = parser.parse_args()
    if os.environ.get("AAN_TOPOLOGY", EXPERIMENT_TOPOLOGY) != EXPERIMENT_TOPOLOGY:
        raise ValueError("overhead measurements support choke_v1 only")
    logging.basicConfig(level=logging.INFO)
    # The frozen YAML schema stays unchanged; argv is archived by run_experiment.started.json.
    system = os.environ["AAN_SYSTEM"]
    if system not in {"ours", "fifo"}:
        raise ValueError("overhead compares only ours and fifo")
    output = Path(os.environ["AAN_OUTPUT_DIR"])
    profile = load_profile(args.profile)
    plan = build_overhead_plan(
        profile,
        pps=args.pps,
        seed=int(os.environ["AAN_SEED"]),
        share=int(os.environ["AAN_AGENT_SHARE_PCT"]),
        burst=os.environ["AAN_BURST_INTENSITY"],
        duration=int(os.environ["AAN_DURATION_S"]),
        link_mbps=int(os.environ["AAN_LINK_MBPS"]),
    )
    pipeline = Path("build/agent_aware.json" if system == "ours" else "build/l2fwd.json")
    inputs = {"profile": args.profile, "pipeline": pipeline}
    if system == "ours":
        inputs["model"] = Path(os.environ["AAN_COMPILED_TREE"])
    archive_inputs(output, inputs)
    write_json(output / "workload-plan.json", plan)
    runtime = RuntimeConfig(
        pipeline,
        output / "network",
        link_mbps=plan["link_mbps"],
        queue_rate_pps=queue_rate_pps(profile, plan["link_mbps"]),
        switch_cpu=args.switch_cpu,
    )
    with network_context(system, runtime, profile, plan) as network:
        # §7.18: this is the owned BMv2 process, never a name-based scan of other switches.
        child = network.get("s1")._bmv2
        if os.sched_getaffinity(child.pid) != {args.switch_cpu}:
            raise RuntimeError("BMv2 CPU affinity differs from the requested single core")
        binary = Path(shutil.which("simple_switch") or "simple_switch").resolve(strict=True)
        before_ticks = read_cpu_ticks(child.pid)
        before_punts = read_punt_packets(system == "ours")
        started = time.monotonic()
        observations = _run_network_traffic(network, plan, profile, output)
        elapsed = time.monotonic() - started
        after_ticks = read_cpu_ticks(child.pid)
        after_punts = read_punt_packets(system == "ours")
        if child.poll() is not None:
            raise RuntimeError("BMv2 exited during overhead measurement")
        punts = None if before_punts is None else after_punts - before_punts
        diagnostic = {
            "system": system,
            "requested_pps": args.pps,
            "switch_cpu": args.switch_cpu,
            "sample_elapsed_s": elapsed,
            "switch_cpu_pct": 100
            * (after_ticks - before_ticks)
            / os.sysconf("SC_CLK_TCK")
            / elapsed,
            "cpu_port_tx_packets": punts,
            "cpu_port_tx_pps": None if punts is None else punts / elapsed,
            "punt_rate_pps": PUNT_RATE_PPS,
            "punt_burst_packets": PUNT_BURST_PACKETS,
            "punt_cap_exceeded": None
            if punts is None
            else punts > PUNT_RATE_PPS * elapsed + PUNT_BURST_PACKETS,
            "switch_argv": child.args,
            "switch_binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
            "switch_log_bytes": (runtime.output_dir / "simple-switch.log").stat().st_size,
            "footprint": pipeline_footprint(pipeline),
            "measurement_note": (
                "Endpoint one-way latency includes host scheduling; CPU sample includes "
                "traffic actor startup/drain. No build-time logging inference."
            ),
        }
        write_json(output / "overhead.json", diagnostic)
    write_json(Path(os.environ["AAN_OBSERVATIONS_PATH"]), observations)
    logger.info("overhead measured for %s at requested %d pps", system, args.pps)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
