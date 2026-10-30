"""Measured latency, goodput, completion, classification and seed-level uncertainty.

Observations are measurements exported by a real workload, not traffic inferred from labels.
Missing arrivals/completions are explicitly censored; unavailable metrics remain JSON null.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from common.contracts import EXPERIMENT_SYSTEMS, FEATURE_CHECKPOINTS, TRAINING_LABELS, TrafficClass

LOGGER = logging.getLogger(__name__)


class MeasurementError(ValueError):
    """Raised for incomplete, nonfinite or inconsistent measurement data."""


class AnchorError(AssertionError):
    """Raised when measured results miss a frozen evaluation target."""


@dataclass(frozen=True)
class ConfidenceInterval:
    """A two-sided 95% Student-t confidence interval over independent seeds."""

    mean: float
    lower: float
    upper: float
    samples: int


@dataclass(frozen=True)
class AnchorResult:
    """Relative improvements evaluated against the frozen acceptance targets."""

    p99_reduction: float
    overhead: float
    best_baseline: str


@dataclass(frozen=True)
class PacketMeasurement:
    """A uniquely matched packet at synchronized sender and receiver taps."""

    packet_id: str
    traffic_class: TrafficClass
    sent_s: float
    received_s: float | None
    size_bytes: int


@dataclass(frozen=True)
class ToolMeasurement:
    """One target-observed tool invocation, including incomplete invocations."""

    call_id: str
    start_s: float
    end_s: float | None


@dataclass(frozen=True)
class ClassificationMeasurement:
    """A single independently labeled flow at one frozen packet checkpoint."""

    flow_id: str
    checkpoint: int
    truth: TrafficClass
    predicted: TrafficClass


def _mapping(raw: object, fields: set[str], context: str) -> Mapping[str, Any]:
    if not isinstance(raw, Mapping) or set(raw) != fields:
        raise MeasurementError(f"{context} must have exactly these fields: {sorted(fields)}")
    return raw


def _number(raw: object, context: str, *, positive: bool = False) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise MeasurementError(f"{context} must be a finite number")
    try:
        value = float(raw)
    except OverflowError as exc:
        raise MeasurementError(f"{context} is too large") from exc
    if not math.isfinite(value) or value < 0 or (positive and value == 0):
        raise MeasurementError(
            f"{context} must be finite and {'positive' if positive else 'nonnegative'}"
        )
    return value


def _identifier(raw: object, context: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise MeasurementError(f"{context} must be a nonempty string")
    return raw


def _class(raw: object) -> TrafficClass:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise MeasurementError("traffic class must be a frozen integer class label")
    try:
        return TrafficClass(raw)
    except ValueError as exc:
        raise MeasurementError(f"invalid traffic class: {raw}") from exc


def _rows(raw: object, context: str) -> list[Any]:
    if not isinstance(raw, list):
        raise MeasurementError(f"{context} must be a JSON list")
    return raw


def _packets(raw: object, start: float, end: float) -> list[PacketMeasurement]:
    packets: list[PacketMeasurement] = []
    seen: set[str] = set()
    for item in _rows(raw, "packets"):
        row = _mapping(
            item, {"packet_id", "traffic_class", "sent_s", "received_s", "size_bytes"}, "packet"
        )
        identity = _identifier(row["packet_id"], "packet_id")
        if identity in seen:
            raise MeasurementError(f"duplicate packet_id: {identity}")
        seen.add(identity)
        sent = _number(row["sent_s"], "sent_s")
        arrival = None if row["received_s"] is None else _number(row["received_s"], "received_s")
        size = row["size_bytes"]
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            raise MeasurementError("size_bytes must be a positive integer")
        if sent < start or sent > end or (arrival is not None and not sent <= arrival <= end):
            raise MeasurementError(
                "packet timestamps must be ordered within the measurement window"
            )
        packets.append(
            PacketMeasurement(identity, _class(row["traffic_class"]), sent, arrival, size)
        )
    if not packets:
        raise MeasurementError("packets cannot be empty: no measured performance evidence")
    return packets


def _tools(raw: object, start: float, end: float) -> list[ToolMeasurement]:
    calls: list[ToolMeasurement] = []
    seen: set[str] = set()
    for item in _rows(raw, "tool_calls"):
        row = _mapping(item, {"call_id", "start_s", "end_s"}, "tool call")
        identity = _identifier(row["call_id"], "call_id")
        if identity in seen:
            raise MeasurementError(f"duplicate call_id: {identity}")
        seen.add(identity)
        began = _number(row["start_s"], "start_s")
        finished = None if row["end_s"] is None else _number(row["end_s"], "end_s")
        if began < start or began > end or (finished is not None and not began <= finished <= end):
            raise MeasurementError("tool timestamps must be ordered within the measurement window")
        calls.append(ToolMeasurement(identity, began, finished))
    return calls


def _classifications(raw: object) -> list[ClassificationMeasurement]:
    predictions: list[ClassificationMeasurement] = []
    seen: set[tuple[str, int]] = set()
    truths: dict[str, TrafficClass] = {}
    for item in _rows(raw, "classifications"):
        row = _mapping(
            item, {"flow_id", "checkpoint", "truth", "predicted", "label_source"}, "classification"
        )
        identity = _identifier(row["flow_id"], "flow_id")
        checkpoint = row["checkpoint"]
        if isinstance(checkpoint, bool) or not isinstance(checkpoint, int):
            raise MeasurementError("checkpoint must be a frozen packet count")
        if checkpoint not in FEATURE_CHECKPOINTS:
            raise MeasurementError(f"checkpoint must be one of {FEATURE_CHECKPOINTS}")
        if (identity, checkpoint) in seen:
            raise MeasurementError("duplicate flow/checkpoint classification")
        seen.add((identity, checkpoint))
        # §7.20: ground truth is orchestration provenance, never classifier features.
        if row["label_source"] != "orchestration":
            raise MeasurementError("ground-truth label_source must be orchestration")
        truth, predicted = _class(row["truth"]), _class(row["predicted"])
        if truth not in TRAINING_LABELS:
            raise MeasurementError("unlabeled background must be excluded from classification P/R")
        if identity in truths and truths[identity] != truth:
            raise MeasurementError("ground truth must agree across a flow's checkpoints")
        truths[identity] = truth
        predictions.append(ClassificationMeasurement(identity, checkpoint, truth, predicted))
    return predictions


def percentiles(samples: Sequence[float]) -> dict[str, float] | None:
    """Return linearly interpolated p50/p95/p99, or null for absent observations."""
    if not samples:
        return None
    ordered = sorted(_number(sample, "percentile sample") for sample in samples)
    result: dict[str, float] = {}
    for name, quantile in (("p50", 0.5), ("p95", 0.95), ("p99", 0.99)):
        index = (len(ordered) - 1) * quantile
        lower = math.floor(index)
        upper = math.ceil(index)
        result[name] = ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)
    return result


def summarize_observations(raw: object) -> dict[str, Any]:
    """Validate strict measured observations and compute a JSON-safe per-run summary.

    All timestamps use one synchronized clock and seconds. Packet latency and tool completion
    are reported in milliseconds; goodput counts received bytes across the full window in Mbps.
    Incomplete observations remain explicit and never enter latency percentiles as zeroes.
    """
    data = _mapping(
        raw,
        {"measurement_start_s", "measurement_end_s", "packets", "tool_calls", "classifications"},
        "observations",
    )
    start = _number(data["measurement_start_s"], "measurement_start_s")
    end = _number(data["measurement_end_s"], "measurement_end_s")
    if end <= start:
        raise MeasurementError("measurement window must have positive duration")
    packets = _packets(data["packets"], start, end)
    tools = _tools(data["tool_calls"], start, end)
    predictions = _classifications(data["classifications"])
    classes: dict[str, Any] = {}
    for label in TrafficClass:
        sent = [packet for packet in packets if packet.traffic_class == label]
        received = [packet for packet in sent if packet.received_s is not None]
        latencies = [(packet.received_s - packet.sent_s) * 1000 for packet in received]
        classes[label.name] = {
            "sent": len(sent),
            "received": len(received),
            "lost": len(sent) - len(received),
            "latency_ms": percentiles(latencies),
            "throughput_mbps": (
                sum(packet.size_bytes for packet in received) * 8 / ((end - start) * 1_000_000)
                if sent
                else None
            ),
        }
    completed = [call for call in tools if call.end_s is not None]
    completion = [(call.end_s - call.start_s) * 1000 for call in completed]
    classification: dict[str, Any] = {}
    for checkpoint in FEATURE_CHECKPOINTS:
        observations = [item for item in predictions if item.checkpoint == checkpoint]
        report: dict[str, Any] = {}
        for label in TrafficClass:
            truth = sum(item.truth == label for item in observations)
            predicted = sum(item.predicted == label for item in observations)
            correct = sum(item.truth == label and item.predicted == label for item in observations)
            report[label.name] = {
                "support": truth,
                "predicted": predicted,
                "true_positive": correct,
                "precision": correct / predicted if predicted else None,
                "recall": correct / truth if truth else None,
            }
        classification[str(checkpoint)] = report
    return {
        "duration_s": end - start,
        "classes": classes,
        "tool_calls": {
            "started": len(tools),
            "completed": len(completed),
            "incomplete": len(tools) - len(completed),
            "completion_fraction": len(completed) / len(tools) if tools else None,
            "completion_ms": percentiles(completion),
        },
        "classification": classification,
    }


def load_observations(path: Path) -> dict[str, Any]:
    """Load and validate raw JSON without modifying the measured source file."""
    raw = _read_json(path)
    # Validate at the boundary; keep the raw form for immutable archival.
    summarize_observations(raw)
    return raw


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key, value in pairs:
        if key in values:
            raise MeasurementError(f"duplicate JSON field: {key}")
        values[key] = value
    return values


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_keys)
    except MeasurementError:
        raise
    except (OSError, UnicodeError, ValueError) as exc:
        raise MeasurementError(f"cannot read measured JSON: {path}") from exc


def _count(raw: object, context: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise MeasurementError(f"{context} must be a nonnegative integer")
    return raw


def _validate_percentiles(raw: object, expected: int) -> None:
    if expected == 0:
        if raw is not None:
            raise MeasurementError("absent samples must have null percentiles")
        return
    report = _mapping(raw, {"p50", "p95", "p99"}, "percentiles")
    values = [_number(report[key], key) for key in ("p50", "p95", "p99")]
    if values != sorted(values):
        raise MeasurementError("percentiles must be monotonically ordered")


def load_plot_summary(path: Path) -> dict[str, Any]:
    """Load raw observations or a strictly validated per-run summary for figure regeneration."""
    raw = _read_json(path)
    if isinstance(raw, dict) and "packets" in raw:
        return summarize_observations(raw)
    summary = _mapping(raw, {"duration_s", "classes", "tool_calls", "classification"}, "summary")
    _number(summary["duration_s"], "duration_s", positive=True)
    classes = _mapping(summary["classes"], {label.name for label in TrafficClass}, "classes")
    for name, value in classes.items():
        report = _mapping(
            value, {"sent", "received", "lost", "latency_ms", "throughput_mbps"}, name
        )
        sent, received, lost = (_count(report[key], key) for key in ("sent", "received", "lost"))
        if sent != received + lost:
            raise MeasurementError("sent packets must equal received plus lost")
        _validate_percentiles(report["latency_ms"], received)
        if sent:
            throughput = _number(report["throughput_mbps"], "throughput_mbps")
            if received == 0 and throughput != 0:
                raise MeasurementError("zero received packets require zero measured throughput")
        elif report["throughput_mbps"] is not None:
            raise MeasurementError("absent class throughput must be null")
    if not sum(report["sent"] for report in classes.values()):
        raise MeasurementError("summary must contain measured packets")
    tools = _mapping(
        summary["tool_calls"],
        {"started", "completed", "incomplete", "completion_fraction", "completion_ms"},
        "tool_calls",
    )
    started, completed, incomplete = (
        _count(tools[key], key) for key in ("started", "completed", "incomplete")
    )
    if started != completed + incomplete:
        raise MeasurementError("tool calls must account for incomplete invocations")
    expected_fraction = completed / started if started else None
    if tools["completion_fraction"] != expected_fraction:
        raise MeasurementError("inconsistent tool completion fraction")
    if expected_fraction is not None:
        _number(tools["completion_fraction"], "completion_fraction")
    _validate_percentiles(tools["completion_ms"], completed)
    checkpoints = _mapping(
        summary["classification"], {str(cp) for cp in FEATURE_CHECKPOINTS}, "classification"
    )
    for reports in checkpoints.values():
        labels = _mapping(reports, {label.name for label in TrafficClass}, "class report")
        for report in labels.values():
            counts = _mapping(
                report,
                {"support", "predicted", "true_positive", "precision", "recall"},
                "precision/recall",
            )
            support, predicted, correct = (
                _count(counts[key], key) for key in ("support", "predicted", "true_positive")
            )
            if correct > min(support, predicted):
                raise MeasurementError("true positives exceed support/predicted counts")
            for metric, denominator in (("precision", predicted), ("recall", support)):
                expected = correct / denominator if denominator else None
                if counts[metric] != expected:
                    raise MeasurementError(f"inconsistent classification {metric}")
                if expected is not None:
                    _number(counts[metric], metric)
    return dict(summary)


def _summary_metrics(summary: dict[str, Any]) -> dict[str, float | None]:
    values: dict[str, float | None] = {}
    for label, report in summary["classes"].items():
        for percentile in ("p50", "p95", "p99"):
            latency = report["latency_ms"]
            values[f"classes.{label}.latency_ms.{percentile}"] = (
                latency[percentile] if latency is not None else None
            )
        values[f"classes.{label}.throughput_mbps"] = report["throughput_mbps"]
    calls = summary["tool_calls"]
    values["tool_calls.completion_fraction"] = calls["completion_fraction"]
    for percentile in ("p50", "p95", "p99"):
        completion = calls["completion_ms"]
        values[f"tool_calls.completion_ms.{percentile}"] = (
            completion[percentile] if completion is not None else None
        )
    for checkpoint, classes in summary["classification"].items():
        for label, report in classes.items():
            for metric in ("precision", "recall"):
                values[f"classification.{checkpoint}.{label}.{metric}"] = report[metric]
    return values


def aggregate_runs(run_dirs: Sequence[Path]) -> list[dict[str, Any]]:
    """Aggregate complete, hash-verified successful runs by config/cell over >=5 seeds.

    Recompute every run's metrics from preserved raw data; never trust editable summary files.
    A metric absent from any seed has a null estimate and reports unavailable_seeds explicitly.
    Every configured seed must be present once; partial grids are not statistical evidence.
    """
    from eval.run_experiment import config_hash, config_values, validate_config

    if not run_dirs:
        raise MeasurementError("at least one measured run directory is required")
    groups: dict[tuple[str, int, str], dict[str, Any]] = {}
    seen_paths: set[Path] = set()
    for run_dir in sorted(run_dirs):
        resolved = run_dir.resolve()
        if resolved in seen_paths:
            raise MeasurementError(f"duplicate run directory: {run_dir}")
        seen_paths.add(resolved)
        started = _read_json(run_dir / "started.json")
        completed = _read_json(run_dir / "completed.json")
        if not isinstance(started, dict) or not isinstance(completed, dict):
            raise MeasurementError("run manifests must be JSON objects")
        if completed.get("status") != "success" or completed.get("returncode") != 0:
            raise MeasurementError(f"run is not a successful measured run: {run_dir}")
        try:
            config = validate_config(started.get("config"))
        except ValueError as exc:
            raise MeasurementError(f"invalid archived config: {run_dir}") from exc
        digest = config_hash(config)
        if started.get("config_hash") != digest:
            raise MeasurementError(f"config hash mismatch: {run_dir}")
        seed = started.get("seed")
        share = started.get("agent_share_pct")
        burst = started.get("burst_intensity")
        if isinstance(seed, bool) or not isinstance(seed, int) or seed not in config.seeds:
            raise MeasurementError("run seed is not present in archived config")
        if (
            isinstance(share, bool)
            or not isinstance(share, int)
            or share not in config.agent_share_pct
        ):
            raise MeasurementError("run share is not present in archived config")
        if burst not in config.burst_intensity:
            raise MeasurementError("run burst is not present in archived config")
        source = run_dir / "observations.json"
        try:
            observed_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        except OSError as exc:
            raise MeasurementError(f"missing measured observations: {source}") from exc
        if observed_hash != completed.get("observations_sha256"):
            raise MeasurementError(f"observations hash mismatch: {run_dir}")
        summary = summarize_observations(load_observations(source))
        key = (digest, share, burst)
        group = groups.setdefault(key, {"config": config_values(config), "runs": {}})
        if seed in group["runs"]:
            raise MeasurementError(f"duplicate seed {seed} in cell {key}")
        group["runs"][seed] = _summary_metrics(summary)
    result: list[dict[str, Any]] = []
    for (digest, share, burst), group in sorted(groups.items()):
        runs = group["runs"]
        expected = group["config"]["seeds"]
        if sorted(runs) != expected or len(runs) < 5:
            raise MeasurementError("each cell requires all configured seeds and at least 5 seeds")
        metrics: dict[str, Any] = {}
        for metric in sorted(next(iter(runs.values()))):
            missing = sorted(seed for seed, values in runs.items() if values[metric] is None)
            estimate = (
                None
                if missing
                else asdict(
                    seed_confidence_interval(
                        {seed: values[metric] for seed, values in runs.items()}
                    )
                )
            )
            metrics[metric] = {"estimate": estimate, "unavailable_seeds": missing}
        result.append(
            {
                "config_hash": digest,
                "config": group["config"],
                "agent_share_pct": share,
                "burst_intensity": burst,
                "seeds": expected,
                "metrics": metrics,
            }
        )
    return result


def seed_confidence_interval(
    samples: Mapping[int, float], *, minimum_seeds: int = 5
) -> ConfidenceInterval:
    """Compute a 95% Student-t CI over seed-level values, never individual packets."""
    if isinstance(minimum_seeds, bool) or not isinstance(minimum_seeds, int) or minimum_seeds < 2:
        raise MeasurementError("minimum_seeds must be an integer >= 2")
    if len(samples) < minimum_seeds:
        raise MeasurementError(f"at least {minimum_seeds} independent seeds are required")
    for seed in samples:
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            raise MeasurementError("seeds must be nonnegative integers")
    values = [_number(samples[seed], "seed metric") for seed in sorted(samples)]
    # SciPy is already a scikit-learn runtime dependency; no new package is introduced.
    from scipy.stats import t

    mean = statistics.mean(values)
    margin = (
        float(t.ppf(0.975, len(values) - 1)) * statistics.stdev(values) / math.sqrt(len(values))
    )
    if not math.isfinite(mean) or not math.isfinite(margin):
        raise MeasurementError("confidence interval overflow")
    return ConfidenceInterval(mean, mean - margin, mean + margin, len(values))


def assert_anchor_targets(
    ours_p99: float,
    baselines_p99: Mapping[str, float],
    pipeline_latency: float,
    l2fwd_latency: float,
) -> AnchorResult:
    """Assert >=30% human p99 reduction vs the best baseline and <5% added latency.

    Callers must compare identical topology/load and pinned hardware (§7.18). Values must
    originate from corresponding independent-seed aggregates, in matching units.
    """
    if set(baselines_p99) != set(EXPERIMENT_SYSTEMS) - {"ours"}:
        raise MeasurementError("all four baseline p99 measurements are required")
    ours = _number(ours_p99, "ours_p99", positive=True)
    baseline = {name: _number(value, name, positive=True) for name, value in baselines_p99.items()}
    pipeline = _number(pipeline_latency, "pipeline_latency", positive=True)
    minimal = _number(l2fwd_latency, "l2fwd_latency", positive=True)
    best = min(baseline, key=lambda name: (baseline[name], name))
    reduction = (baseline[best] - ours) / baseline[best]
    overhead = (pipeline - minimal) / minimal
    failures = []
    if reduction < 0.30:
        failures.append(f"p99 reduction {reduction:.6f} < 0.30 vs {best}")
    if overhead >= 0.05:
        failures.append(f"overhead {overhead:.6f} >= 0.05")
    if failures:
        LOGGER.error("anchor FAIL: %s", "; ".join(failures))
        raise AnchorError("; ".join(failures))
    return AnchorResult(reduction, overhead, best)


def write_json(path: Path, value: object) -> None:
    """Create a durable JSON artifact exclusively; never overwrite an earlier result."""
    import os

    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def main(argv: Sequence[str] | None = None) -> int:
    """Extract metrics from measured observations into a new summary JSON artifact."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("observations", type=Path, nargs="?")
    parser.add_argument(
        "--runs", type=Path, nargs="+", help="successful run dirs for seed-level CIs"
    )
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    try:
        if bool(args.runs) == (args.observations is not None):
            raise MeasurementError("provide observations or --runs, exclusively")
        summary = (
            aggregate_runs(args.runs)
            if args.runs
            else summarize_observations(load_observations(args.observations))
        )
        write_json(args.output, summary)
    except (MeasurementError, OSError) as exc:
        LOGGER.error("metrics failed: %s", exc)
        return 1
    LOGGER.info("measured summary written to %s", args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
