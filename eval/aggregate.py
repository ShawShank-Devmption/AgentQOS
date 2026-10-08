"""Aggregate immutable completed runs into audited metrics and centerpiece inputs."""

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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common.contracts import EXPERIMENT_SYSTEMS, TRAINING_LABELS
from dashboard.producer import build_snapshot_from_packets, read_telemetry
from eval.metrics import MetricInputError, confidence_interval_95

LOGGER = logging.getLogger(__name__)
CLASS_METRICS = ("throughput_mbps", "p50_ms", "p95_ms", "p99_ms")


class AggregateError(ValueError):
    """Raised when completed run evidence is missing, unbalanced, or inconsistent."""


@dataclass(frozen=True)
class RunArtifact:
    """Validated manifest, summary, and filesystem location for one run."""

    run_dir: Path
    manifest: Mapping[str, object]
    summary: Mapping[str, object]


def aggregate_results(results_root: Path, output_dir: Path) -> tuple[Path, ...]:
    """Validate and aggregate completed experiment cells.

    Args:
        results_root: Tree containing immutable run directories and manifests.
        output_dir: New destination for aggregate CSV files.

    Returns:
        Paths to the audit, centerpiece, confidence-interval, and per-run CSVs.
    """
    artifacts = _load_artifacts(results_root)
    statistical_artifacts = _multi_seed_artifacts(artifacts)
    run_rows = _run_metric_rows(statistical_artifacts)
    confidence_rows = _confidence_rows(run_rows)
    centerpiece_rows = _centerpiece_rows(statistical_artifacts)
    audit_rows = _audit_rows(artifacts, statistical_artifacts, results_root)
    output_dir.mkdir(parents=True, exist_ok=False)
    outputs = (
        output_dir / "audit.csv",
        output_dir / "centerpiece.csv",
        output_dir / "confidence_intervals.csv",
        output_dir / "run_metrics.csv",
    )
    _write_csv(
        outputs[0],
        (
            "config_hash",
            "seed",
            "system",
            "included_in_statistics",
            "manifest",
            "run_summary",
            "run_summary_sha256",
        ),
        audit_rows,
    )
    _write_csv(outputs[1], ("time_s", "system", "human_p99_ms"), centerpiece_rows)
    _write_csv(
        outputs[2],
        (
            "system",
            "agent_share_pct",
            "burst_intensity",
            "class",
            "metric",
            "sample_count",
            "mean",
            "ci_low",
            "ci_high",
        ),
        confidence_rows,
    )
    _write_csv(
        outputs[3],
        (
            "config_hash",
            "seed",
            "system",
            "agent_share_pct",
            "burst_intensity",
            "class",
            *CLASS_METRICS,
            "tool_completion_p99_ms",
            "agent_attempted_requests",
            "agent_failed_requests",
            "agent_failure_rate",
            "corpus_verification_rate",
        ),
        run_rows,
    )
    return outputs


def main(argv: Sequence[str] | None = None) -> int:
    """Aggregate one completed results tree from the command line.

    Args:
        argv: Optional command-line arguments.

    Returns:
        Zero when aggregation succeeds, otherwise one.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results_root", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        outputs = aggregate_results(args.results_root, args.output_dir)
    except (AggregateError, FileExistsError, FileNotFoundError, MetricInputError, OSError) as exc:
        LOGGER.error("aggregation failed: %s", exc)
        return 1
    LOGGER.info("wrote %d aggregate files", len(outputs))
    return 0


def _load_artifacts(results_root: Path) -> tuple[RunArtifact, ...]:
    if not results_root.is_dir():
        raise FileNotFoundError(results_root)
    manifest_paths = tuple(sorted(results_root.rglob("manifest.json")))
    if not manifest_paths:
        raise AggregateError(f"no run manifests found below {results_root}")
    artifacts: list[RunArtifact] = []
    for manifest_path in manifest_paths:
        manifest = _read_mapping(manifest_path)
        if manifest.get("status") != "complete":
            raise AggregateError(f"run is not complete: {manifest_path}")
        summary_path = manifest_path.with_name("run_summary.json")
        try:
            summary_bytes = summary_path.read_bytes()
        except OSError as exc:
            raise AggregateError(f"could not read run summary: {summary_path}") from exc
        digest = hashlib.sha256(summary_bytes).hexdigest()
        if manifest.get("run_summary_sha256") != digest:
            raise AggregateError(f"run summary SHA-256 mismatch: {summary_path}")
        summary = _read_mapping(summary_path)
        _validate_coordinates(manifest, summary, summary_path)
        _validate_summary_metrics(summary, summary_path)
        telemetry_path = manifest_path.with_name("packet_telemetry.tsv")
        if not telemetry_path.is_file():
            raise AggregateError(f"run omitted packet telemetry: {telemetry_path}")
        ingress_path = manifest_path.with_name("ingress_packet_telemetry.tsv")
        if not ingress_path.is_file():
            raise AggregateError(f"run omitted ingress packet telemetry: {ingress_path}")
        artifacts.append(RunArtifact(manifest_path.parent, manifest, summary))
    return tuple(artifacts)


def _run_metric_rows(artifacts: Sequence[RunArtifact]) -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    for artifact in artifacts:
        manifest = artifact.manifest
        summary = artifact.summary
        classes = _mapping(summary["classes"], "classes")
        completion = _mapping(summary["tool_completion"], "tool_completion")
        attempts = _mapping(summary["agent_attempts"], "agent_attempts")
        corpus = _mapping(summary["corpus"], "corpus")
        attempted = _number(attempts["attempted"], "agent_attempts.attempted")
        failed = _number(attempts["failed"], "agent_attempts.failed")
        if attempted <= 0 or failed > attempted:
            raise AggregateError("agent attempt counts are inconsistent")
        for traffic_class in TRAINING_LABELS:
            class_metrics = _mapping(classes[traffic_class.name], traffic_class.name)
            rows.append(
                {
                    "config_hash": manifest["config_hash"],
                    "seed": manifest["seed"],
                    "system": manifest["system"],
                    "agent_share_pct": manifest["agent_share_pct"],
                    "burst_intensity": manifest["burst_intensity"],
                    "class": traffic_class.name,
                    **{metric: _number(class_metrics[metric], metric) for metric in CLASS_METRICS},
                    "tool_completion_p99_ms": _number(completion["p99_ms"], "p99_ms"),
                    "agent_attempted_requests": attempted,
                    "agent_failed_requests": failed,
                    "agent_failure_rate": failed / attempted,
                    "corpus_verification_rate": _number(
                        corpus["verification_rate"], "verification_rate"
                    ),
                }
            )
    return tuple(rows)


def _confidence_rows(run_rows: Sequence[Mapping[str, object]]) -> tuple[dict[str, object], ...]:
    samples: dict[tuple[str, int, str, str, str], list[float]] = defaultdict(list)
    for row in run_rows:
        group_prefix = (
            str(row["system"]),
            int(row["agent_share_pct"]),
            str(row["burst_intensity"]),
            str(row["class"]),
        )
        for metric in CLASS_METRICS:
            samples[(*group_prefix, metric)].append(float(row[metric]))
        if row["class"] == "HUMAN_INTERACTIVE":
            samples[
                (
                    str(row["system"]),
                    int(row["agent_share_pct"]),
                    str(row["burst_intensity"]),
                    "ALL_AGENTS",
                    "tool_completion_p99_ms",
                )
            ].append(float(row["tool_completion_p99_ms"]))
            samples[
                (
                    str(row["system"]),
                    int(row["agent_share_pct"]),
                    str(row["burst_intensity"]),
                    "ALL_AGENTS",
                    "agent_failure_rate",
                )
            ].append(float(row["agent_failure_rate"]))
    rows: list[dict[str, object]] = []
    for (system, share, burst, class_name, metric), values in sorted(samples.items()):
        if len(values) < 2:
            raise AggregateError(
                f"confidence interval requires at least two seeds: "
                f"{system}/{share}/{burst}/{class_name}/{metric}"
            )
        interval = confidence_interval_95(values)
        rows.append(
            {
                "system": system,
                "agent_share_pct": share,
                "burst_intensity": burst,
                "class": class_name,
                "metric": metric,
                "sample_count": interval.sample_count,
                "mean": interval.mean,
                "ci_low": interval.low,
                "ci_high": interval.high,
            }
        )
    return tuple(rows)


def _centerpiece_rows(artifacts: Sequence[RunArtifact]) -> tuple[dict[str, object], ...]:
    by_system: dict[str, list[RunArtifact]] = defaultdict(list)
    for artifact in artifacts:
        if artifact.manifest["burst_intensity"] == "high":
            by_system[str(artifact.manifest["system"])].append(artifact)
    if set(by_system) != set(EXPERIMENT_SYSTEMS):
        raise AggregateError("centerpiece requires high-burst completed runs for all five systems")
    common_shares = set.intersection(
        *(
            {int(artifact.manifest["agent_share_pct"]) for artifact in by_system[system]}
            for system in EXPERIMENT_SYSTEMS
        )
    )
    if not common_shares:
        raise AggregateError("centerpiece systems have no common high-burst agent share")
    share = max(common_shares)
    selected = {
        system: [
            artifact
            for artifact in by_system[system]
            if int(artifact.manifest["agent_share_pct"]) == share
        ]
        for system in EXPERIMENT_SYSTEMS
    }
    seed_sets = [
        {int(artifact.manifest["seed"]) for artifact in selected[system]}
        for system in EXPERIMENT_SYSTEMS
    ]
    if len(seed_sets[0]) < 2 or any(seeds != seed_sets[0] for seeds in seed_sets[1:]):
        raise AggregateError("centerpiece requires balanced multi-seed coverage across systems")
    durations = {
        int(artifact.manifest["duration_s"])
        for system in EXPERIMENT_SYSTEMS
        for artifact in selected[system]
    }
    if len(durations) != 1:
        raise AggregateError("centerpiece runs must have one common duration")
    duration_s = durations.pop()
    samples: dict[tuple[str, int], list[float]] = defaultdict(list)
    for system in EXPERIMENT_SYSTEMS:
        for artifact in selected[system]:
            packets = read_telemetry(artifact.run_dir / "packet_telemetry.tsv")
            ingress_packets = read_telemetry(artifact.run_dir / "ingress_packet_telemetry.tsv")
            start_s = _number(artifact.summary["measurement_start_s"], "measurement_start_s")
            for offset_s in range(1, duration_s + 1):
                snapshot = build_snapshot_from_packets(
                    packets,
                    ingress_packets=ingress_packets,
                    now_s=start_s + offset_s,
                    window_s=1.0,
                )
                classes = _mapping(snapshot["classes"], "classes")
                human = _mapping(classes["HUMAN_INTERACTIVE"], "HUMAN_INTERACTIVE")
                samples[(system, offset_s)].append(_number(human["p99_ms"], "p99_ms"))
    return tuple(
        {
            "time_s": offset_s,
            "system": system,
            "human_p99_ms": statistics.fmean(samples[(system, offset_s)]),
        }
        for system in EXPERIMENT_SYSTEMS
        for offset_s in range(1, duration_s + 1)
    )


def _audit_rows(
    artifacts: Sequence[RunArtifact],
    statistical_artifacts: Sequence[RunArtifact],
    results_root: Path,
) -> tuple[dict[str, object], ...]:
    included = {artifact.run_dir for artifact in statistical_artifacts}
    return tuple(
        {
            "config_hash": artifact.manifest["config_hash"],
            "seed": artifact.manifest["seed"],
            "system": artifact.manifest["system"],
            "included_in_statistics": artifact.run_dir in included,
            "manifest": (artifact.run_dir / "manifest.json").relative_to(results_root).as_posix(),
            "run_summary": (artifact.run_dir / "run_summary.json")
            .relative_to(results_root)
            .as_posix(),
            "run_summary_sha256": artifact.manifest["run_summary_sha256"],
        }
        for artifact in artifacts
    )


def _multi_seed_artifacts(artifacts: Sequence[RunArtifact]) -> tuple[RunArtifact, ...]:
    by_config: dict[tuple[str, str], list[RunArtifact]] = defaultdict(list)
    for artifact in artifacts:
        key = (
            str(artifact.manifest["config_hash"]),
            str(artifact.manifest["system"]),
        )
        by_config[key].append(artifact)
    eligible = {
        key
        for key, grouped in by_config.items()
        if len({int(artifact.manifest["seed"]) for artifact in grouped}) >= 2
    }
    selected = tuple(
        artifact
        for artifact in artifacts
        if (str(artifact.manifest["config_hash"]), str(artifact.manifest["system"])) in eligible
    )
    if not selected:
        raise AggregateError("no multi-seed experiment configs were found")
    return selected


def _validate_coordinates(
    manifest: Mapping[str, object],
    summary: Mapping[str, object],
    summary_path: Path,
) -> None:
    for field in (
        "system",
        "link_mbps",
        "agent_share_pct",
        "burst_intensity",
        "duration_s",
        "seed",
    ):
        if manifest.get(field) != summary.get(field):
            raise AggregateError(f"coordinate mismatch for {field}: {summary_path}")


def _validate_summary_metrics(summary: Mapping[str, object], summary_path: Path) -> None:
    if summary.get("latency_method") != "matched_two_tap":
        raise AggregateError(f"run summary did not use matched two-tap latency: {summary_path}")
    latency_match = _mapping(summary.get("latency_match"), "latency_match")
    eligible = _number(latency_match.get("eligible_packets"), "eligible_packets")
    matched = _number(latency_match.get("matched_packets"), "matched_packets")
    coverage = _number(latency_match.get("coverage"), "latency_match.coverage")
    if eligible <= 0 or matched <= 0 or matched > eligible:
        raise AggregateError(f"invalid two-tap match counts: {summary_path}")
    if not math.isclose(coverage, matched / eligible, rel_tol=1e-12, abs_tol=1e-12):
        raise AggregateError(f"invalid two-tap match coverage: {summary_path}")
    classes = _mapping(summary.get("classes"), "classes")
    required_classes = {traffic_class.name for traffic_class in TRAINING_LABELS}
    if set(classes) != required_classes:
        raise AggregateError(f"invalid class coverage: {summary_path}")
    for class_name in required_classes:
        metrics = _mapping(classes[class_name], class_name)
        if set(metrics) != set(CLASS_METRICS):
            raise AggregateError(f"invalid metrics for {class_name}: {summary_path}")
        for metric in CLASS_METRICS:
            _number(metrics[metric], metric)
    completion = _mapping(summary.get("tool_completion"), "tool_completion")
    attempts = _mapping(summary.get("agent_attempts"), "agent_attempts")
    corpus = _mapping(summary.get("corpus"), "corpus")
    _number(completion.get("p99_ms"), "tool_completion.p99_ms")
    attempted = _number(attempts.get("attempted"), "agent_attempts.attempted")
    completed = _number(attempts.get("completed"), "agent_attempts.completed")
    failed = _number(attempts.get("failed"), "agent_attempts.failed")
    if attempted <= 0 or completed + failed != attempted:
        raise AggregateError(f"inconsistent agent attempt counts: {summary_path}")
    verification_rate = _number(corpus.get("verification_rate"), "verification_rate")
    if verification_rate > 1:
        raise AggregateError(f"verification_rate exceeds one: {summary_path}")
    start_s = _number(summary.get("measurement_start_s"), "measurement_start_s")
    end_s = _number(summary.get("measurement_end_s"), "measurement_end_s")
    if end_s <= start_s:
        raise AggregateError(f"invalid measurement interval: {summary_path}")


def _read_mapping(path: Path) -> Mapping[str, object]:
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AggregateError(f"could not parse JSON object: {path}") from exc
    return _mapping(raw, str(path))


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise AggregateError(f"{name} must be an object with string keys")
    return value


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AggregateError(f"{name} must be numeric")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise AggregateError(f"{name} must be finite and non-negative")
    return number


def _write_csv(
    path: Path,
    fields: tuple[str, ...],
    rows: Sequence[Mapping[str, object]],
) -> None:
    with path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
