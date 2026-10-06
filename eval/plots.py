"""Deterministically regenerate paper figures from strict aggregate CSV inputs."""

from __future__ import annotations

import argparse
import csv
import logging
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

LOGGER = logging.getLogger(__name__)
FIGURE_NAMES = (
    "fig2_human_p99_timeline.png",
    "fig3_precision_recall.png",
    "fig4_overhead_cdf.png",
    "fig5_scaling.png",
    "fig6_evasion.png",
    "table_feature_importance.csv",
)
_COLORS = ("#0F766E", "#DC2626", "#2563EB", "#7C3AED", "#D97706")


class PlotInputError(ValueError):
    """Raised when aggregate figure data is absent or malformed."""


def generate_figures(raw_dir: Path, output_dir: Path) -> tuple[Path, ...]:
    """Generate Figures 2-6 and the feature-importance table.

    Args:
        raw_dir: Directory containing the six strict aggregate CSV inputs.
        output_dir: Destination created only after every input validates.

    Returns:
        Output paths in paper figure order.

    Raises:
        FileNotFoundError: If an input CSV does not exist.
        PlotInputError: If a required column or numeric value is invalid.
    """
    centerpiece = _read_csv(raw_dir / "centerpiece.csv", ("time_s", "system", "human_p99_ms"))
    classification = _read_csv(raw_dir / "classification.csv", ("class", "precision", "recall"))
    overhead = _read_csv(raw_dir / "overhead.csv", ("pipeline", "latency_ms"))
    scaling = _read_csv(
        raw_dir / "scaling.csv",
        ("concurrent_flows", "accuracy", "memory_bytes", "collision_rate"),
    )
    evasion = _read_csv(raw_dir / "evasion.csv", ("think_time_ms", "accuracy", "throughput_tps"))
    importances = _read_csv(raw_dir / "feature_importance.csv", ("feature", "importance"))
    _validate_numeric(centerpiece, ("time_s", "human_p99_ms"), "centerpiece.csv")
    _validate_numeric(classification, ("precision", "recall"), "classification.csv")
    _validate_numeric(overhead, ("latency_ms",), "overhead.csv")
    _validate_numeric(
        scaling,
        ("concurrent_flows", "accuracy", "memory_bytes", "collision_rate"),
        "scaling.csv",
    )
    _validate_numeric(evasion, ("think_time_ms", "accuracy", "throughput_tps"), "evasion.csv")
    _validate_numeric(importances, ("importance",), "feature_importance.csv")

    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = tuple(output_dir / name for name in FIGURE_NAMES)
    with plt.rc_context(
        {
            "font.family": "DejaVu Sans",
            "font.size": 9,
            "axes.grid": True,
            "grid.alpha": 0.25,
            "figure.dpi": 100,
            "savefig.dpi": 150,
        }
    ):
        _plot_centerpiece(centerpiece, outputs[0])
        _plot_classification(classification, outputs[1])
        _plot_overhead(overhead, outputs[2])
        _plot_scaling(scaling, outputs[3])
        _plot_evasion(evasion, outputs[4])
    _write_importance_table(importances, outputs[5])
    return outputs


def main(argv: Sequence[str] | None = None) -> int:
    """Generate all figures from one aggregate-data directory.

    Args:
        argv: Optional command-line arguments.

    Returns:
        Zero when every output is generated.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        outputs = generate_figures(args.raw_dir, args.output_dir)
    except (FileNotFoundError, PlotInputError) as exc:
        LOGGER.error("figure generation failed: %s", exc)
        return 1
    LOGGER.info("generated %d paper outputs", len(outputs))
    return 0


def _read_csv(path: Path, required: tuple[str, ...]) -> tuple[dict[str, str], ...]:
    if not path.is_file():
        raise FileNotFoundError(path)
    try:
        with path.open(encoding="utf-8", newline="") as input_file:
            reader = csv.DictReader(input_file)
            fields = set(reader.fieldnames or ())
            missing = set(required) - fields
            if missing:
                raise PlotInputError(f"{path.name} missing columns: {sorted(missing)}")
            rows = tuple(dict(row) for row in reader)
    except (OSError, UnicodeError, csv.Error) as exc:
        raise PlotInputError(f"could not parse figure input: {path}") from exc
    if not rows:
        raise PlotInputError(f"{path.name} contains no rows")
    return rows


def _validate_numeric(
    rows: Sequence[Mapping[str, str]],
    fields: tuple[str, ...],
    name: str,
) -> None:
    for line_number, row in enumerate(rows, start=2):
        for field in fields:
            try:
                float(row[field])
            except (KeyError, TypeError, ValueError) as exc:
                raise PlotInputError(f"{name} has invalid {field} on line {line_number}") from exc


def _plot_centerpiece(rows: Sequence[Mapping[str, str]], output: Path) -> None:
    grouped: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for row in rows:
        grouped[row["system"]].append((float(row["time_s"]), float(row["human_p99_ms"])))
    figure, axis = plt.subplots(figsize=(7.2, 3.8), constrained_layout=True)
    for color, system in zip(_COLORS, sorted(grouped), strict=False):
        points = sorted(grouped[system])
        axis.plot(
            [point[0] for point in points],
            [point[1] for point in points],
            label=system,
            color=color,
            linewidth=2,
        )
    axis.set(xlabel="Time (s)", ylabel="Human p99 latency (ms)")
    axis.legend(frameon=False, ncol=3)
    _save(figure, output)


def _plot_classification(rows: Sequence[Mapping[str, str]], output: Path) -> None:
    ordered = sorted(rows, key=lambda row: row["class"])
    positions = list(range(len(ordered)))
    figure, axis = plt.subplots(figsize=(7.2, 3.8), constrained_layout=True)
    axis.bar(
        [position - 0.19 for position in positions],
        [float(row["precision"]) for row in ordered],
        width=0.38,
        label="Precision",
        color=_COLORS[0],
    )
    axis.bar(
        [position + 0.19 for position in positions],
        [float(row["recall"]) for row in ordered],
        width=0.38,
        label="Recall",
        color=_COLORS[2],
    )
    axis.set_xticks(positions, [row["class"].replace("_", "\n") for row in ordered])
    axis.set(ylim=(0, 1), ylabel="Score")
    axis.legend(frameon=False)
    _save(figure, output)


def _plot_overhead(rows: Sequence[Mapping[str, str]], output: Path) -> None:
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        grouped[row["pipeline"]].append(float(row["latency_ms"]))
    figure, axis = plt.subplots(figsize=(7.2, 3.8), constrained_layout=True)
    for color, pipeline in zip(_COLORS, sorted(grouped), strict=False):
        values = sorted(grouped[pipeline])
        cumulative = [(index + 1) / len(values) for index in range(len(values))]
        axis.step(values, cumulative, where="post", label=pipeline, color=color, linewidth=2)
    axis.set(xlabel="Per-packet latency (ms)", ylabel="CDF", ylim=(0, 1.01))
    axis.legend(frameon=False)
    _save(figure, output)


def _plot_scaling(rows: Sequence[Mapping[str, str]], output: Path) -> None:
    ordered = sorted(rows, key=lambda row: float(row["concurrent_flows"]))
    flows = [float(row["concurrent_flows"]) for row in ordered]
    figure, left = plt.subplots(figsize=(7.2, 3.8), constrained_layout=True)
    right = left.twinx()
    left.plot(flows, [float(row["accuracy"]) for row in ordered], label="Accuracy")
    left.plot(
        flows,
        [float(row["collision_rate"]) for row in ordered],
        label="Collision rate",
        linestyle="--",
    )
    right.plot(
        flows,
        [float(row["memory_bytes"]) / 1024 for row in ordered],
        color=_COLORS[3],
        label="Memory",
    )
    left.set(xscale="log", xlabel="Concurrent flows", ylabel="Fraction", ylim=(0, 1.01))
    right.set_ylabel("Memory (KiB)")
    handles_left, labels_left = left.get_legend_handles_labels()
    handles_right, labels_right = right.get_legend_handles_labels()
    left.legend(handles_left + handles_right, labels_left + labels_right, frameon=False)
    _save(figure, output)


def _plot_evasion(rows: Sequence[Mapping[str, str]], output: Path) -> None:
    ordered = sorted(rows, key=lambda row: float(row["think_time_ms"]))
    think_time = [float(row["think_time_ms"]) for row in ordered]
    figure, left = plt.subplots(figsize=(7.2, 3.8), constrained_layout=True)
    right = left.twinx()
    left.plot(think_time, [float(row["accuracy"]) for row in ordered], label="Accuracy")
    right.plot(
        think_time,
        [float(row["throughput_tps"]) for row in ordered],
        label="Agent throughput",
        color=_COLORS[1],
    )
    left.set(xlabel="Injected think time (ms)", ylabel="Classifier accuracy", ylim=(0, 1.01))
    right.set_ylabel("Agent throughput (tasks/s)")
    handles_left, labels_left = left.get_legend_handles_labels()
    handles_right, labels_right = right.get_legend_handles_labels()
    left.legend(handles_left + handles_right, labels_left + labels_right, frameon=False)
    _save(figure, output)


def _write_importance_table(rows: Sequence[Mapping[str, str]], output: Path) -> None:
    ordered = sorted(rows, key=lambda row: (-float(row["importance"]), row["feature"]))
    with output.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=("feature", "importance"))
        writer.writeheader()
        writer.writerows(
            {"feature": row["feature"], "importance": f"{float(row['importance']):.8f}"}
            for row in ordered
        )


def _save(figure: plt.Figure, output: Path) -> None:
    figure.savefig(
        output,
        format="png",
        dpi=150,
        metadata={"Software": "AgentQOS deterministic plots"},
    )
    plt.close(figure)


if __name__ == "__main__":
    raise SystemExit(main())
