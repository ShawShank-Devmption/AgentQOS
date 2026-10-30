"""Deterministic standalone diagnostic figures from archived measured observations."""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from common.contracts import FEATURE_CHECKPOINTS, TRAINING_LABELS, TrafficClass  # noqa: E402
from eval.metrics import MeasurementError, load_plot_summary  # noqa: E402

LOGGER = logging.getLogger(__name__)


def _bar_figure(
    target: Path, title: str, ylabel: str, labels: Sequence[str], values: Sequence[float | None]
) -> Path:
    with plt.rc_context({"font.family": "DejaVu Sans", "font.size": 9}):
        figure, axis = plt.subplots(figsize=(8, 4), layout="constrained")
        for index, value in enumerate(values):
            if value is None:
                axis.annotate("unavailable", (index, 0), rotation=90, ha="center", va="bottom")
            else:
                axis.bar(index, value, width=0.65, color="#34669a")
        axis.set_xticks(range(len(labels)), labels, rotation=20, ha="right")
        axis.set_title(title)
        axis.set_ylabel(ylabel)
        axis.set_ylim(bottom=0)
        axis.grid(axis="y", alpha=0.2)
        try:
            # Fixed metadata removes runtime-dependent timestamps from PNG artifacts.
            figure.savefig(target, dpi=160, metadata={"Software": "Agent-Aware Networking"})
        finally:
            plt.close(figure)
    return target


def _render_summary(summary: dict[str, Any], output_dir: Path) -> tuple[Path, ...]:
    labels = [label.name for label in TrafficClass]
    classes = summary["classes"]
    latency = [classes[name]["latency_ms"] for name in labels]
    outputs = [
        _bar_figure(
            output_dir / "latency.png",
            "Measured per-class packet latency (p99)",
            "Latency (ms)",
            labels,
            [value["p99"] if value is not None else None for value in latency],
        ),
        _bar_figure(
            output_dir / "throughput.png",
            "Measured received throughput",
            "Throughput (Mbps)",
            labels,
            [classes[name]["throughput_mbps"] for name in labels],
        ),
    ]
    calls = summary["tool_calls"]
    completion = calls["completion_ms"]
    outputs.append(
        _bar_figure(
            output_dir / "tool_completion.png",
            f"Tool completion: {calls['completed']}/{calls['started']} completed; "
            f"{calls['incomplete']} incomplete",
            "Completion time (ms)",
            ["p50", "p95", "p99"],
            [completion[key] if completion is not None else None for key in ("p50", "p95", "p99")],
        )
    )
    prediction_labels: list[str] = []
    values: list[float | None] = []
    for checkpoint in FEATURE_CHECKPOINTS:
        for label in TRAINING_LABELS:
            for metric in ("precision", "recall"):
                prediction_labels.append(f"{checkpoint}: {label.name}\n{metric}")
                values.append(summary["classification"][str(checkpoint)][label.name][metric])
    outputs.append(
        _bar_figure(
            output_dir / "classification.png",
            "Classification by packet checkpoint",
            "Fraction",
            prediction_labels,
            values,
        )
    )
    return tuple(outputs)


def render_figures(source: Path, output_dir: Path) -> tuple[Path, ...]:
    """Render four deterministic diagnostic figures while preserving raw input bytes.

    Unavailable observations are annotated, never displayed as measured zeroes. These figures
    describe one archived run; comparative paper conclusions require the independent-seed grid.
    """
    summary = load_plot_summary(source)
    output_dir.mkdir(parents=True, exist_ok=True)
    targets = ("latency.png", "throughput.png", "tool_completion.png", "classification.png")
    if any((output_dir / name).exists() for name in targets):
        raise FileExistsError("figure output already exists; choose a new directory")
    return _render_summary(summary, output_dir)


def main(argv: Sequence[str] | None = None) -> int:
    """Regenerate standalone PNGs from one measured observations.json file."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("observations", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    try:
        outputs = render_figures(args.observations, args.output_dir)
    except (MeasurementError, OSError) as exc:
        LOGGER.error("figure regeneration failed: %s", exc)
        return 1
    LOGGER.info("generated %d figures in %s", len(outputs), args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
