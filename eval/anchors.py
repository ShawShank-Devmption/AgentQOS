"""Evaluate and attest the two predeclared headline experiment anchors."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import math
import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from common.contracts import EXPERIMENT_SYSTEMS
from eval.metrics import AnchorResult, MetricInputError, evaluate_anchors

LOGGER = logging.getLogger(__name__)
PREDECLARED_MIN_P99_REDUCTION = 0.30
PREDECLARED_MAX_OVERHEAD = 0.05
OVERHEAD_FIELDS = (
    "pair_id",
    "seed",
    "host_id",
    "load_profile",
    "sample_id",
    "pipeline",
    "latency_ms",
)


class AnchorInputError(ValueError):
    """Raised when audited anchor inputs are absent, malformed, or unbalanced."""


@dataclass(frozen=True)
class AnchorReport:
    """Input means, strictest baseline, and explicit anchor results."""

    best_baseline: str
    ours_p99_ms: float
    baseline_p99_ms: float
    full_pipeline_latency_ms: float
    minimal_pipeline_latency_ms: float
    result: AnchorResult


def evaluate_anchor_files(
    centerpiece_path: Path,
    overhead_path: Path,
    output_path: Path,
) -> AnchorReport:
    """Evaluate anchors from strict CSV inputs and write an attested JSON result.

    Args:
        centerpiece_path: Balanced five-system human-p99 timeline from `eval.aggregate`.
        overhead_path: Per-packet minimal and full pipeline latency samples.
        output_path: New JSON destination; existing evidence is never overwritten.
    Returns:
        The complete anchor report, including explicit pass flags.
    """
    centerpiece_rows = _read_csv(
        centerpiece_path,
        ("time_s", "system", "human_p99_ms"),
    )
    overhead_rows = _read_csv(overhead_path, OVERHEAD_FIELDS)
    system_samples, time_coordinates = _centerpiece_samples(centerpiece_rows)
    if set(system_samples) != set(EXPERIMENT_SYSTEMS):
        raise AnchorInputError("centerpiece must contain exactly all five experiment systems")
    if len({frozenset(values) for values in time_coordinates.values()}) != 1:
        raise AnchorInputError("centerpiece systems must have the same time coordinates")
    means = {system: statistics.fmean(values) for system, values in system_samples.items()}
    baseline_means = {system: value for system, value in means.items() if system != "ours"}
    best_baseline = min(baseline_means, key=baseline_means.__getitem__)
    overhead_samples = _overhead_samples(overhead_rows)
    result = evaluate_anchors(
        ours_p99_ms=means["ours"],
        baseline_p99_ms=baseline_means[best_baseline],
        full_pipeline_latency_ms=statistics.fmean(overhead_samples["full"]),
        minimal_pipeline_latency_ms=statistics.fmean(overhead_samples["minimal"]),
        min_p99_reduction=PREDECLARED_MIN_P99_REDUCTION,
        max_overhead=PREDECLARED_MAX_OVERHEAD,
    )
    report = AnchorReport(
        best_baseline=best_baseline,
        ours_p99_ms=means["ours"],
        baseline_p99_ms=baseline_means[best_baseline],
        full_pipeline_latency_ms=statistics.fmean(overhead_samples["full"]),
        minimal_pipeline_latency_ms=statistics.fmean(overhead_samples["minimal"]),
        result=result,
    )
    payload = {
        "inputs": {
            "centerpiece": str(centerpiece_path),
            "centerpiece_sha256": _sha256(centerpiece_path),
            "overhead": str(overhead_path),
            "overhead_sha256": _sha256(overhead_path),
        },
        "thresholds": {
            "min_p99_reduction": PREDECLARED_MIN_P99_REDUCTION,
            "max_overhead": PREDECLARED_MAX_OVERHEAD,
        },
        "best_baseline": report.best_baseline,
        "measurements": {
            "ours_p99_ms": report.ours_p99_ms,
            "baseline_p99_ms": report.baseline_p99_ms,
            "full_pipeline_latency_ms": report.full_pipeline_latency_ms,
            "minimal_pipeline_latency_ms": report.minimal_pipeline_latency_ms,
        },
        "result": asdict(result),
        "passed": result.p99_passed and result.overhead_passed,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as output_file:
        json.dump(payload, output_file, indent=2, sort_keys=True)
        output_file.write("\n")
    return report


def main(argv: Sequence[str] | None = None) -> int:
    """Evaluate anchor files, preserving a report for both pass and fail outcomes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("centerpiece", type=Path)
    parser.add_argument("overhead", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        report = evaluate_anchor_files(
            args.centerpiece,
            args.overhead,
            args.output,
        )
    except (AnchorInputError, FileExistsError, FileNotFoundError, MetricInputError, OSError) as exc:
        LOGGER.error("anchor evaluation failed: %s", exc)
        return 1
    passed = report.result.p99_passed and report.result.overhead_passed
    log = LOGGER.info if passed else LOGGER.error
    log(
        "anchors %s: p99_reduction=%.6f overhead=%.6f best_baseline=%s",
        "PASS" if passed else "FAIL",
        report.result.p99_reduction,
        report.result.pipeline_overhead,
        report.best_baseline,
    )
    return 0 if passed else 2


def _read_csv(path: Path, expected_fields: tuple[str, ...]) -> tuple[dict[str, str], ...]:
    if not path.is_file():
        raise FileNotFoundError(path)
    try:
        with path.open(encoding="utf-8", newline="") as input_file:
            reader = csv.DictReader(input_file)
            if tuple(reader.fieldnames or ()) != expected_fields:
                raise AnchorInputError(f"{path.name} fields must be exactly {expected_fields}")
            rows = tuple(dict(row) for row in reader)
    except (OSError, UnicodeError, csv.Error) as exc:
        raise AnchorInputError(f"could not parse anchor input: {path}") from exc
    if not rows:
        raise AnchorInputError(f"anchor input contains no rows: {path}")
    return rows


def _centerpiece_samples(
    rows: Sequence[Mapping[str, str]],
) -> tuple[dict[str, list[float]], dict[str, set[float]]]:
    samples: dict[str, list[float]] = defaultdict(list)
    coordinates: dict[str, set[float]] = defaultdict(set)
    for line_number, row in enumerate(rows, start=2):
        system = row["system"]
        try:
            time_s = float(row["time_s"])
            p99_ms = float(row["human_p99_ms"])
        except ValueError as exc:
            raise AnchorInputError(f"invalid centerpiece number on line {line_number}") from exc
        if (
            system not in EXPERIMENT_SYSTEMS
            or not math.isfinite(time_s)
            or time_s < 0
            or not math.isfinite(p99_ms)
            or p99_ms < 0
        ):
            raise AnchorInputError(f"invalid centerpiece row on line {line_number}")
        if time_s in coordinates[system]:
            raise AnchorInputError(f"duplicate centerpiece coordinate on line {line_number}")
        coordinates[system].add(time_s)
        samples[system].append(p99_ms)
    return dict(samples), dict(coordinates)


def _overhead_samples(rows: Sequence[Mapping[str, str]]) -> dict[str, list[float]]:
    samples: dict[str, list[float]] = defaultdict(list)
    coordinates: dict[tuple[str, int, str, str, str], set[str]] = defaultdict(set)
    for line_number, row in enumerate(rows, start=2):
        pipeline = row["pipeline"]
        try:
            seed = int(row["seed"])
            latency_ms = float(row["latency_ms"])
        except ValueError as exc:
            raise AnchorInputError(f"invalid overhead number on line {line_number}") from exc
        identity_fields = ("pair_id", "host_id", "load_profile", "sample_id")
        if (
            pipeline not in {"minimal", "full"}
            or seed < 0
            or any(not row[field].strip() for field in identity_fields)
            or not math.isfinite(latency_ms)
            or latency_ms < 0
        ):
            raise AnchorInputError(f"invalid overhead row on line {line_number}")
        coordinate = (
            row["pair_id"],
            seed,
            row["host_id"],
            row["load_profile"],
            row["sample_id"],
        )
        if pipeline in coordinates[coordinate]:
            raise AnchorInputError(f"duplicate overhead coordinate on line {line_number}")
        coordinates[coordinate].add(pipeline)
        samples[pipeline].append(latency_ms)
    if set(samples) != {"minimal", "full"}:
        raise AnchorInputError("overhead must contain minimal and full pipeline samples")
    if any(pipelines != {"minimal", "full"} for pipelines in coordinates.values()):
        raise AnchorInputError("overhead samples must be paired on pair/seed/host/load/sample")
    if len({coordinate[1] for coordinate in coordinates}) < 5:
        raise AnchorInputError("overhead requires at least five paired seeds")
    return dict(samples)


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise AnchorInputError(f"could not hash anchor input: {path}") from exc


if __name__ == "__main__":
    raise SystemExit(main())
