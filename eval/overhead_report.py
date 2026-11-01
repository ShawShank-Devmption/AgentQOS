"""C4/P4.3: compare completed, matching FIFO/full-pipeline overhead measurements."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common.contracts import TrafficClass
from eval.metrics import load_observations, percentiles, write_json


@dataclass(frozen=True)
class OverheadRun:
    """A completed run and its measured diagnostics; never inferred performance."""

    path: Path
    system: str
    pps: int
    seed: int
    host: str
    plan: dict[str, Any]
    diagnostics: dict[str, Any]
    hashes: dict[str, str]
    latency: dict[str, float] | None
    human_latency: dict[str, float] | None
    sent: int
    lost: int
    achieved_pps: float


def load_run(path: Path) -> OverheadRun:
    """Reject incomplete or inconsistent evidence before calculating overhead."""
    completed = json.loads((path / "completed.json").read_text())
    if completed.get("status") != "success":
        raise ValueError(f"run did not complete successfully: {path}")
    raw_bytes = (path / "observations.json").read_bytes()
    if hashlib.sha256(raw_bytes).hexdigest() != completed.get("observations_sha256"):
        raise ValueError(f"observations hash mismatch: {path}")
    raw = load_observations(path / "observations.json")
    started = json.loads((path / "started.json").read_text())
    plan = json.loads((path / "workload-plan.json").read_text())
    diagnostics = json.loads((path / "overhead.json").read_text())
    hashes = json.loads((path / "inputs/sha256.json").read_text())
    for name, digest in hashes.items():
        if hashlib.sha256((path / "inputs" / f"{name}.json").read_bytes()).hexdigest() != digest:
            raise ValueError(f"archived input hash mismatch: {path}/{name}")
    system = diagnostics["system"]
    pps = diagnostics["requested_pps"]
    if system not in {"fifo", "ours"} or type(pps) is not int or pps <= 0:
        raise ValueError("overhead run must identify fifo/ours and a positive integer pps")
    if started["config"]["system"] != system or started["seed"] != plan["seed"]:
        raise ValueError("run metadata disagrees with overhead diagnostics")
    if not {"profile", "pipeline"} <= hashes.keys() or (system == "ours" and "model" not in hashes):
        raise ValueError("missing archived profile, pipeline or model")
    cpu = diagnostics["switch_cpu_pct"]
    if (
        isinstance(cpu, bool)
        or not isinstance(cpu, (float, int))
        or not math.isfinite(cpu)
        or cpu < 0
    ):
        raise ValueError("invalid measured switch CPU percentage")
    all_latencies = []
    human_latencies = []
    lost = 0
    for packet in raw["packets"]:
        if packet["received_s"] is None:
            lost += 1
            continue
        latency = (packet["received_s"] - packet["sent_s"]) * 1000
        all_latencies.append(latency)
        if packet["traffic_class"] == int(TrafficClass.HUMAN_INTERACTIVE):
            human_latencies.append(latency)
    profile = json.loads((path / "inputs/profile.json").read_text())
    horizon = plan["duration_s"] - profile["drain_s"]
    if horizon <= 0:
        raise ValueError("invalid offered-traffic horizon")
    planned = sum(len(flow["offsets_s"]) for flow in plan.get("flows", []))
    if abs(planned - pps * horizon) > max(1, len(plan.get("flows", []))):
        raise ValueError("packet schedule does not match the requested offered rate")
    if len(raw["packets"]) != planned:
        raise ValueError("sender did not complete the configured packet schedule")
    return OverheadRun(
        path,
        system,
        pps,
        plan["seed"],
        started["host"],
        plan,
        diagnostics,
        hashes,
        percentiles(all_latencies),
        percentiles(human_latencies),
        len(raw["packets"]),
        lost,
        len(raw["packets"]) / horizon,
    )


def compare_runs(paths: list[Path]) -> dict[str, Any]:
    """Pair identical plans/host conditions and report the highest tested passing rate."""
    pairs: dict[tuple[int, int], dict[str, OverheadRun]] = {}
    for path in paths:
        run = load_run(path)
        pair = pairs.setdefault((run.pps, run.seed), {})
        if run.system in pair:
            raise ValueError("duplicate system for a rate/seed pair")
        pair[run.system] = run
    if not pairs:
        raise ValueError("at least one matched pair is required")
    rows = []
    conditions = None
    model_hash = None
    pipeline_hashes: dict[str, str] = {}
    for (pps, seed), pair in sorted(pairs.items()):
        if set(pair) != {"ours", "fifo"}:
            raise ValueError(f"missing ours/fifo partner at {pps} pps seed {seed}")
        ours, fifo = pair["ours"], pair["fifo"]
        if ours.plan != fifo.plan:
            raise ValueError("paired runs must have identical packet schedules")
        if ours.sent != fifo.sent:
            raise ValueError("paired runs sent different packet counts")
        for run in (fifo, ours):
            current = (
                run.host,
                run.diagnostics["switch_cpu"],
                run.diagnostics["switch_binary_sha256"],
                run.hashes["profile"],
            )
            if conditions is not None and current != conditions:
                raise ValueError("host, CPU, binary or workload profile differs across sweep")
            conditions = current
            if (
                run.system in pipeline_hashes
                and pipeline_hashes[run.system] != run.hashes["pipeline"]
            ):
                raise ValueError("pipeline differs across sweep")
            pipeline_hashes[run.system] = run.hashes["pipeline"]
        if model_hash is not None and ours.hashes["model"] != model_hash:
            raise ValueError("compiled model differs across sweep")
        model_hash = ours.hashes["model"]
        ratio = None
        if ours.latency is not None and fifo.latency is not None and fifo.latency["p99"] > 0:
            ratio = ours.latency["p99"] / fifo.latency["p99"]
        human_ratio = None
        if (
            ours.human_latency is not None
            and fifo.human_latency is not None
            and fifo.human_latency["p99"] > 0
        ):
            human_ratio = ours.human_latency["p99"] / fifo.human_latency["p99"]
        rows.append(
            {
                "requested_pps": pps,
                "seed": seed,
                "added_p99_fraction": None if ratio is None else ratio - 1,
                "human_p99_ratio": human_ratio,
                "latency_anchor_pass": None if ratio is None else ratio - 1 < 0.05,
                "loss_free": ours.lost == fifo.lost == 0,
                "runs": [
                    {
                        "system": run.system,
                        "path": str(run.path),
                        "latency_ms": run.latency,
                        "human_latency_ms": run.human_latency,
                        "cpu_pct": run.diagnostics["switch_cpu_pct"],
                        "cpu_port_tx_packets": run.diagnostics["cpu_port_tx_packets"],
                        "cpu_port_tx_pps": run.diagnostics["cpu_port_tx_pps"],
                        "punt_cap_exceeded": run.diagnostics["punt_cap_exceeded"],
                        "footprint": run.diagnostics["footprint"],
                        "sent": run.sent,
                        "lost": run.lost,
                        "achieved_pps": run.achieved_pps,
                    }
                    for run in (fifo, ours)
                ],
            }
        )
    rates = sorted({row["requested_pps"] for row in rows})
    passing = [
        pps
        for pps in rates
        if all(
            row["latency_anchor_pass"] is True
            and row["loss_free"]
            and row["runs"][1]["punt_cap_exceeded"] is False
            for row in rows
            if row["requested_pps"] == pps
        )
    ]
    return {
        "pairs": rows,
        "highest_tested_loss_free_passing_pps": max(passing, default=None),
        "note": "Highest passing sampled rate is not a global maximum; inspect achieved pps.",
    }


def markdown_report(report: dict[str, Any]) -> str:
    """Render measured aggregate percentiles without averaging class percentiles."""
    lines = [
        "# C4 measured overhead",
        "",
        "| pps | seed | pipeline | achieved pps | p50 ms | p99 ms | CPU % | punts/s | lost |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for pair in report["pairs"]:
        for run in pair["runs"]:
            latency = run["latency_ms"]
            values = [
                pair["requested_pps"],
                pair["seed"],
                run["system"],
                run["achieved_pps"],
                None if latency is None else latency["p50"],
                None if latency is None else latency["p99"],
                run["cpu_pct"],
                run["cpu_port_tx_pps"],
                run["lost"],
            ]
            lines.append(
                "| "
                + " | ".join(
                    "unavailable"
                    if value is None
                    else f"{value:.6g}"
                    if isinstance(value, float)
                    else str(value)
                    for value in values
                )
                + " |"
            )
    lines.extend(["", report["note"], ""])
    return "\n".join(lines)


def main() -> None:
    """Write new report artifacts; preserve every source run unchanged."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = compare_runs(args.runs)
    # §7.19: report generation cannot replace previous evidence.
    args.output.mkdir(parents=True, exist_ok=False)
    write_json(args.output / "overhead-report.json", report)
    (args.output / "overhead-report.md").write_text(markdown_report(report), encoding="utf-8")


if __name__ == "__main__":
    main()
