"""P4.4 Fig. 5 from complete live scaling cells, with seed-level confidence intervals."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from eval.metrics import seed_confidence_interval, write_json  # noqa: E402
from eval.run_experiment import load_config  # noqa: E402
from eval.state_scaling import load_profile  # noqa: E402


def aggregate_scaling(
    paths: list[Path], experiment: Path, profile_path: Path
) -> list[dict[str, Any]]:
    """Require every configured seed/count, marking infeasible concurrency unavailable."""
    config = load_config(experiment)
    profile = load_profile(profile_path)
    if len(config.seeds) < 5:
        raise ValueError("publication scaling data requires at least five configured seeds")
    cells: dict[tuple[int, int], dict[str, Any]] = {}
    conditions = None
    for path in paths:
        completion = json.loads((path / "completed.json").read_text())
        if completion.get("status") != "success":
            raise ValueError("scaling plot refuses a failed or incomplete experiment")
        if (
            completion["observations_sha256"]
            != hashlib.sha256((path / "observations.json").read_bytes()).hexdigest()
        ):
            raise ValueError("scaling observations hash mismatch")
        started = json.loads((path / "started.json").read_text())
        report = json.loads((path / "analysis/scaling-report.json").read_text())
        hashes = json.loads((path / "inputs/sha256.json").read_text())
        for name in ("profile", "pipeline", "model"):
            if (
                hashes[name]
                != hashlib.sha256((path / "inputs" / f"{name}.json").read_bytes()).hexdigest()
            ):
                raise ValueError("scaling archived input hash mismatch")
        if hashes["profile"] != hashlib.sha256(profile_path.read_bytes()).hexdigest():
            raise ValueError("scaling profile differs from the requested sweep")
        if report["provenance"] != "real_switch_captures_synthetic_stress_workload":
            raise ValueError("scaling plots require live capture reports, not analytical plans")
        key = report["requested_flows"], report["seed"]
        if key in cells:
            raise ValueError("duplicate scaling flow-count/seed cell")
        if started["seed"] != report["seed"] or started["config"]["system"] != "ours":
            raise ValueError("scaling report disagrees with experiment metadata")
        for name in ("topology", "link_mbps", "duration_s"):
            if started["config"][name] != getattr(config, name):
                raise ValueError(f"scaling experiment differs in {name}")
        if (
            started["agent_share_pct"] not in config.agent_share_pct
            or started["burst_intensity"] not in config.burst_intensity
        ):
            raise ValueError("scaling experiment share/burst differs")
        runtime = report["conditions"]
        if runtime is None:
            raise ValueError("scaling switch conditions are unavailable")
        current = (
            started["host"],
            hashes["pipeline"],
            hashes["model"],
            runtime["switch_cpu"],
            runtime["switch_binary_sha256"],
        )
        if conditions is not None and current != conditions:
            raise ValueError("scaling host, CPU, binary, pipeline or model differs across sweep")
        conditions = current
        cells[key] = report
    expected = {(count, seed) for count in profile["flow_counts"] for seed in config.seeds}
    if set(cells) != expected:
        raise ValueError("scaling sweep is missing configured cells or contains extra cells")
    checkpoint = profile["packets_per_flow"]
    rows = []
    for count in profile["flow_counts"]:
        reports = [cells[count, seed] for seed in config.seeds]
        valid = all(report["requested_concurrency_observed"] for report in reports)
        row: dict[str, Any] = {"flows": count, "concurrency_valid_all_seeds": valid}
        metrics = {
            "collision_packet_fraction": [
                r["collision_events_per_ingress_packet"] for r in reports
            ],
            "accuracy_all_offered": [
                r["accuracy"][str(checkpoint)]["correct_fraction_all_offered"] for r in reports
            ],
            "switch_rss_bytes": [r["switch_rss_bytes"] for r in reports],
            "logical_register_bits": [r["footprint"]["register_bits"] for r in reports],
        }
        for name, values in metrics.items():
            interval = (
                seed_confidence_interval(dict(zip(config.seeds, values, strict=True)))
                if valid and all(v is not None for v in values)
                else None
            )
            row[name] = (
                None
                if interval is None
                else {
                    "mean": interval.mean,
                    "lower": interval.lower,
                    "upper": interval.upper,
                    "samples": interval.samples,
                }
            )
        rows.append(row)
    return rows


def render_scaling_figure(rows: list[dict[str, Any]], output: Path) -> None:
    """Export a standalone scientific figure; infeasible points stay unavailable."""
    output.mkdir(parents=True, exist_ok=False)
    with plt.rc_context({"font.size": 9}):
        figure, axes = plt.subplots(1, 3, figsize=(12, 3.6), layout="constrained")
        try:
            for axis, metric, ylabel, scale in (
                (axes[0], "collision_packet_fraction", "Collision packet fraction", 1),
                (axes[1], "accuracy_all_offered", "Correct fraction of offered flows", 1),
                (axes[2], "switch_rss_bytes", "Switch resident memory MiB", 1 / (1024 * 1024)),
            ):
                for row in rows:
                    interval = row[metric]
                    x = row["flows"]
                    if interval is None:
                        axis.annotate(
                            "unavailable", (x, 0), rotation=90, fontsize=7, ha="center", va="bottom"
                        )
                        continue
                    mean = interval["mean"] * scale
                    axis.errorbar(
                        x,
                        mean,
                        yerr=[
                            [max(0, mean - interval["lower"] * scale)],
                            [max(0, interval["upper"] * scale - mean)],
                        ],
                        fmt="o",
                        color="#245b80",
                        capsize=3,
                    )
                axis.set_xscale("log")
                axis.set_xlabel("Offered concurrent flows")
                axis.set_ylabel(ylabel)
                axis.set_ylim(bottom=0)
                axis.grid(alpha=0.25)
            figure.suptitle("Synthetic stress on BMv2 with 95 percent seed confidence intervals")
            figure.savefig(
                output / "fig5.png", dpi=180, metadata={"Software": "Agent-Aware Networking"}
            )
            figure.savefig(output / "fig5.pdf", metadata={"CreationDate": None, "ModDate": None})
        finally:
            plt.close(figure)
    write_json(output / "scaling-aggregate.json", rows)


def main() -> None:
    """Plot only archived live experiments, never the analytical ownership plan."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", type=Path, nargs="+")
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--profile", type=Path, default=Path("eval/configs/state_scaling.json"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = aggregate_scaling(args.runs, args.experiment, args.profile)
    render_scaling_figure(rows, args.output)


if __name__ == "__main__":
    main()
