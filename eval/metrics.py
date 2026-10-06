"""Verified metric calculations for captured experiment outputs."""

from __future__ import annotations

import csv
import json
import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from common.contracts import TRAINING_LABELS, TrafficClass

# Two-sided 95% Student-t critical values indexed by degrees of freedom 1..30.
_T_CRITICAL_95 = (
    0.0,
    12.706204736,
    4.30265273,
    3.182446305,
    2.776445105,
    2.570581836,
    2.446911851,
    2.364624252,
    2.306004135,
    2.262157163,
    2.228138852,
    2.20098516,
    2.17881283,
    2.160368656,
    2.144786688,
    2.131449546,
    2.119905299,
    2.109815578,
    2.10092204,
    2.093024054,
    2.085963447,
    2.079613845,
    2.073873068,
    2.06865761,
    2.063898562,
    2.059538553,
    2.055529439,
    2.051830516,
    2.048407142,
    2.045229642,
    2.042272456,
)


class MetricInputError(ValueError):
    """Raised when a metric input file or sample violates its schema."""


@dataclass(frozen=True)
class PercentileSummary:
    """Latency percentiles in milliseconds."""

    count: int
    p50_ms: float
    p95_ms: float
    p99_ms: float


@dataclass(frozen=True)
class ClassScore:
    """One-vs-rest confusion counts and derived class metrics."""

    true_positive: int
    false_positive: int
    false_negative: int
    precision: float | None
    recall: float | None


@dataclass(frozen=True)
class ConfidenceInterval:
    """Mean and two-sided 95% confidence interval."""

    sample_count: int
    mean: float
    low: float
    high: float


@dataclass(frozen=True)
class AnchorResult:
    """Explicit pass/fail result for the two headline evaluation anchors."""

    p99_reduction: float
    pipeline_overhead: float
    p99_passed: bool
    overhead_passed: bool


def percentile_summary(latencies_ms: Sequence[float]) -> PercentileSummary:
    """Calculate linearly interpolated p50, p95, and p99 latencies.

    Args:
        latencies_ms: Non-empty finite, non-negative latency samples.

    Returns:
        Sample count and three percentiles in milliseconds.

    Raises:
        MetricInputError: If any sample is invalid or the input is empty.
    """
    values = _validated_samples(latencies_ms, "latency")
    return PercentileSummary(
        count=len(values),
        p50_ms=_percentile(values, 0.50),
        p95_ms=_percentile(values, 0.95),
        p99_ms=_percentile(values, 0.99),
    )


def completion_times(log_path: Path) -> tuple[float, ...]:
    """Read successful tool-call durations from target JSONL logs.

    Args:
        log_path: Instrumented MCP target request log.

    Returns:
        Successful completion times in milliseconds and file order.

    Raises:
        FileNotFoundError: If the log does not exist.
        MetricInputError: If a non-empty entry is malformed.
    """
    if not log_path.is_file():
        raise FileNotFoundError(log_path)
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise MetricInputError(f"could not read completion log: {log_path}") from exc
    values: list[float] = []
    for line_number, raw_line in enumerate(lines, start=1):
        if not raw_line.strip():
            continue
        try:
            entry = json.loads(raw_line)
            if not isinstance(entry, Mapping):
                raise TypeError("entry must be an object")
            start_ns = _strict_int(entry["start_time_ns"])
            end_ns = _strict_int(entry["end_time_ns"])
            status = entry["status"]
            if status not in {"ok", "error"}:
                raise ValueError("invalid status")
            if end_ns < start_ns:
                raise ValueError("end precedes start")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise MetricInputError(
                f"invalid completion log entry on line {line_number}: {log_path}"
            ) from exc
        if status == "ok":
            values.append((end_ns - start_ns) / 1_000_000)
    return tuple(values)


def classification_metrics(
    ground_truth: Sequence[TrafficClass],
    predicted: Sequence[TrafficClass],
) -> dict[TrafficClass, ClassScore]:
    """Calculate one-vs-rest precision and recall for all three training classes.

    Args:
        ground_truth: Orchestration labels; UNKNOWN is not permitted.
        predicted: Switch/controller labels; UNKNOWN is permitted.

    Returns:
        Scores keyed by the frozen training classes.

    Raises:
        MetricInputError: If lengths differ, inputs are empty, or labels are invalid.
    """
    if not ground_truth:
        raise MetricInputError("classification inputs must not be empty")
    if len(ground_truth) != len(predicted):
        raise MetricInputError("ground_truth and predicted lengths must match")
    truth_values = tuple(_traffic_class(value) for value in ground_truth)
    predicted_values = tuple(_traffic_class(value) for value in predicted)
    if any(value not in TRAINING_LABELS for value in truth_values):
        raise MetricInputError("ground_truth cannot contain UNKNOWN")

    scores: dict[TrafficClass, ClassScore] = {}
    for traffic_class in TRAINING_LABELS:
        true_positive = sum(
            truth == traffic_class and prediction == traffic_class
            for truth, prediction in zip(truth_values, predicted_values, strict=True)
        )
        false_positive = sum(
            truth != traffic_class and prediction == traffic_class
            for truth, prediction in zip(truth_values, predicted_values, strict=True)
        )
        false_negative = sum(
            truth == traffic_class and prediction != traffic_class
            for truth, prediction in zip(truth_values, predicted_values, strict=True)
        )
        precision_denominator = true_positive + false_positive
        recall_denominator = true_positive + false_negative
        scores[traffic_class] = ClassScore(
            true_positive=true_positive,
            false_positive=false_positive,
            false_negative=false_negative,
            precision=(true_positive / precision_denominator if precision_denominator else None),
            recall=true_positive / recall_denominator if recall_denominator else None,
        )
    return scores


def confidence_interval_95(samples: Sequence[float]) -> ConfidenceInterval:
    """Calculate a two-sided 95% Student-t confidence interval.

    Args:
        samples: Non-empty finite observations, normally one per seed.

    Returns:
        Mean, interval endpoints, and sample count.

    Raises:
        MetricInputError: If samples are empty or non-finite.
    """
    values = _validated_samples(samples, "confidence-interval sample", non_negative=False)
    mean = statistics.fmean(values)
    if len(values) == 1:
        return ConfidenceInterval(1, mean, mean, mean)
    degrees_of_freedom = len(values) - 1
    critical = (
        _T_CRITICAL_95[degrees_of_freedom]
        if degrees_of_freedom < len(_T_CRITICAL_95)
        else 1.959963985
    )
    margin = critical * statistics.stdev(values) / math.sqrt(len(values))
    return ConfidenceInterval(len(values), mean, mean - margin, mean + margin)


def evaluate_anchors(
    *,
    ours_p99_ms: float,
    baseline_p99_ms: float,
    full_pipeline_latency_ms: float,
    minimal_pipeline_latency_ms: float,
    min_p99_reduction: float,
    max_overhead: float,
) -> AnchorResult:
    """Evaluate configured p99-reduction and pipeline-overhead thresholds.

    Args:
        ours_p99_ms: Human p99 latency with the proposed system.
        baseline_p99_ms: Human p99 latency with the comparison baseline.
        full_pipeline_latency_ms: Full-pipeline per-packet latency.
        minimal_pipeline_latency_ms: Minimal-l2fwd per-packet latency.
        min_p99_reduction: Required fractional p99 reduction.
        max_overhead: Maximum allowed fractional overhead.

    Returns:
        Measured fractions and explicit pass flags.

    Raises:
        MetricInputError: If denominators or thresholds are invalid.
    """
    values = (
        ours_p99_ms,
        baseline_p99_ms,
        full_pipeline_latency_ms,
        minimal_pipeline_latency_ms,
    )
    if any(not math.isfinite(value) or value < 0 for value in values):
        raise MetricInputError("anchor latency values must be finite and non-negative")
    if baseline_p99_ms == 0 or minimal_pipeline_latency_ms == 0:
        raise MetricInputError("anchor denominators must be positive")
    if not 0 <= min_p99_reduction <= 1 or not 0 <= max_overhead <= 1:
        raise MetricInputError("anchor thresholds must be fractions between zero and one")
    reduction = (baseline_p99_ms - ours_p99_ms) / baseline_p99_ms
    overhead = (full_pipeline_latency_ms - minimal_pipeline_latency_ms) / (
        minimal_pipeline_latency_ms
    )
    return AnchorResult(
        p99_reduction=reduction,
        pipeline_overhead=overhead,
        p99_passed=reduction >= min_p99_reduction,
        overhead_passed=overhead < max_overhead,
    )


def read_latency_csv(path: Path) -> tuple[float, ...]:
    """Read a strict `latency_ms` CSV column.

    Args:
        path: CSV file with a `latency_ms` header.

    Returns:
        Validated latency samples in file order.

    Raises:
        FileNotFoundError: If the CSV does not exist.
        MetricInputError: If its schema or values are invalid.
    """
    if not path.is_file():
        raise FileNotFoundError(path)
    try:
        with path.open(encoding="utf-8", newline="") as input_file:
            reader = csv.DictReader(input_file)
            if reader.fieldnames is None or "latency_ms" not in reader.fieldnames:
                raise MetricInputError("latency CSV must contain a latency_ms column")
            values: list[float] = []
            for line_number, row in enumerate(reader, start=2):
                try:
                    value = float(row["latency_ms"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise MetricInputError(
                        f"invalid latency_ms value on line {line_number}: {path}"
                    ) from exc
                if not math.isfinite(value) or value < 0:
                    raise MetricInputError(
                        f"invalid latency_ms value on line {line_number}: {path}"
                    )
                values.append(value)
    except (OSError, UnicodeError, csv.Error) as exc:
        raise MetricInputError(f"could not parse latency CSV: {path}") from exc
    if not values:
        raise MetricInputError("latency CSV contains no samples")
    return tuple(values)


def _validated_samples(
    samples: Sequence[float],
    name: str,
    *,
    non_negative: bool = True,
) -> tuple[float, ...]:
    if not samples:
        raise MetricInputError(f"{name} samples must not be empty")
    values = tuple(float(value) for value in samples)
    if any(not math.isfinite(value) or (non_negative and value < 0) for value in values):
        raise MetricInputError(f"{name} samples must be finite and valid")
    return tuple(sorted(values))


def _percentile(sorted_values: Sequence[float], quantile: float) -> float:
    position = (len(sorted_values) - 1) * quantile
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    if lower_index == upper_index:
        return sorted_values[lower_index]
    weight = position - lower_index
    return sorted_values[lower_index] * (1 - weight) + sorted_values[upper_index] * weight


def _traffic_class(value: TrafficClass) -> TrafficClass:
    if isinstance(value, TrafficClass):
        return value
    try:
        return TrafficClass(value)
    except ValueError as exc:
        raise MetricInputError(f"invalid traffic class: {value}") from exc


def _strict_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("value must be an integer")
    return value
