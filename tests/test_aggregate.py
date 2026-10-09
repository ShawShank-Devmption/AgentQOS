"""Tests for traceable aggregation of completed experiment cells."""

import csv
import hashlib
import json
from pathlib import Path

import pytest

from common.contracts import EXPERIMENT_SYSTEMS, TRAINING_LABELS
from eval.aggregate import AggregateError, aggregate_results


def test_aggregate_results_emits_audited_runs_cis_and_five_system_timeline(
    tmp_path: Path,
) -> None:
    results = tmp_path / "results"
    for system_index, system in enumerate(EXPERIMENT_SYSTEMS):
        for share in (10, 30, 50, 70, 90):
            for burst in ("low", "med", "high"):
                for seed in (1, 2, 3, 4, 5):
                    _write_completed_run(
                        results,
                        system,
                        seed,
                        0.010 + system_index * 0.001,
                        agent_share_pct=share,
                        burst_intensity=burst,
                    )
    _write_completed_run(
        results,
        "fifo",
        99,
        0.050,
        config_name="fifo-sanity",
        burst_intensity="low",
    )

    outputs = aggregate_results(results, tmp_path / "aggregates")

    assert {path.name for path in outputs} == {
        "audit.csv",
        "centerpiece.csv",
        "confidence_intervals.csv",
        "run_metrics.csv",
    }
    centerpiece = _read_csv(tmp_path / "aggregates" / "centerpiece.csv")
    assert {row["system"] for row in centerpiece} == set(EXPERIMENT_SYSTEMS)
    assert {(row["system"], row["time_s"]) for row in centerpiece} == {
        (system, time_s) for system in EXPERIMENT_SYSTEMS for time_s in ("1", "2")
    }
    ours_at_one = next(
        row for row in centerpiece if row["system"] == "ours" and row["time_s"] == "1"
    )
    assert float(ours_at_one["human_p99_ms"]) == pytest.approx(10.0)

    run_metrics = _read_csv(tmp_path / "aggregates" / "run_metrics.csv")
    assert len(run_metrics) == len(EXPERIMENT_SYSTEMS) * 5 * 3 * 5 * len(TRAINING_LABELS)
    confidence = _read_csv(tmp_path / "aggregates" / "confidence_intervals.csv")
    human_p99 = next(
        row
        for row in confidence
        if row["system"] == "ours"
        and row["class"] == "HUMAN_INTERACTIVE"
        and row["metric"] == "p99_ms"
    )
    assert human_p99["sample_count"] == "5"
    assert float(human_p99["mean"]) == pytest.approx(10.0)
    audit = _read_csv(tmp_path / "aggregates" / "audit.csv")
    assert len(audit) == 376
    sanity = next(row for row in audit if row["config_hash"] == "fifo-sanity")
    assert sanity["included_in_statistics"] == "False"


def test_aggregate_rejects_summary_hash_mismatch(tmp_path: Path) -> None:
    results = tmp_path / "results"
    run_dir = _write_completed_run(results, "ours", 1, 0.010)
    summary_path = run_dir / "run_summary.json"
    summary_path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(AggregateError, match="SHA-256"):
        aggregate_results(results, tmp_path / "aggregates")


def test_aggregate_rejects_packet_telemetry_hash_mismatch(tmp_path: Path) -> None:
    results = tmp_path / "results"
    run_dir = _write_completed_run(results, "ours", 1, 0.010)
    (run_dir / "packet_telemetry.tsv").write_text("tampered\n", encoding="utf-8")

    with pytest.raises(AggregateError, match="packet telemetry SHA-256"):
        aggregate_results(results, tmp_path / "aggregates")


def test_aggregate_rejects_missing_ingress_telemetry(tmp_path: Path) -> None:
    results = tmp_path / "results"
    run_dir = _write_completed_run(results, "ours", 1, 0.010)
    (run_dir / "ingress_packet_telemetry.tsv").unlink()

    with pytest.raises(AggregateError, match="omitted ingress packet telemetry"):
        aggregate_results(results, tmp_path / "aggregates")


def test_aggregate_rejects_a_grid_with_fewer_than_five_seeds(tmp_path: Path) -> None:
    results = tmp_path / "results"
    for system in EXPERIMENT_SYSTEMS:
        for seed in (1, 2, 3, 4):
            _write_completed_run(results, system, seed, 0.010)

    with pytest.raises(AggregateError, match="five-seed"):
        aggregate_results(results, tmp_path / "aggregates")


def test_aggregate_rejects_centerpiece_interval_without_a_human_match(tmp_path: Path) -> None:
    results = tmp_path / "results"
    run_dir = Path()
    for system in EXPERIMENT_SYSTEMS:
        for share in (10, 30, 50, 70, 90):
            for burst in ("low", "med", "high"):
                for seed in (1, 2, 3, 4, 5):
                    candidate = _write_completed_run(
                        results,
                        system,
                        seed,
                        0.010,
                        agent_share_pct=share,
                        burst_intensity=burst,
                    )
                    if system == "app_limiter" and share == 90 and burst == "high" and seed == 5:
                        run_dir = candidate
    target_path = run_dir / "packet_telemetry.tsv"
    target_path.write_text(
        "".join(
            line
            for line in target_path.read_text(encoding="utf-8").splitlines(keepends=True)
            if not line.startswith("101.5\t10.0.0.2\t")
        ),
        encoding="utf-8",
    )
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["packet_telemetry_sha256"] = hashlib.sha256(target_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(AggregateError, match="human two-tap match"):
        aggregate_results(results, tmp_path / "aggregates")


def test_aggregate_rejects_duplicate_eligible_config_coordinates(tmp_path: Path) -> None:
    results = tmp_path / "results"
    for system in EXPERIMENT_SYSTEMS:
        for share in (10, 30, 50, 70, 90):
            for burst in ("low", "med", "high"):
                for seed in (1, 2, 3, 4, 5):
                    _write_completed_run(
                        results,
                        system,
                        seed,
                        0.010,
                        agent_share_pct=share,
                        burst_intensity=burst,
                    )
    for seed in (1, 2, 3, 4, 5):
        _write_completed_run(
            results,
            "ours",
            seed,
            0.010,
            config_name="hash-ours-rerun",
        )

    with pytest.raises(AggregateError, match="duplicate statistical"):
        aggregate_results(results, tmp_path / "aggregates")


def _write_completed_run(
    root: Path,
    system: str,
    seed: int,
    human_rtt_s: float,
    *,
    config_name: str | None = None,
    burst_intensity: str = "high",
    agent_share_pct: int = 90,
) -> Path:
    config_hash = config_name or f"hash-{system}"
    run_dir = (
        root / config_hash / f"{system}-share-{agent_share_pct}-burst-{burst_intensity}-seed-{seed}"
    )
    run_dir.mkdir(parents=True)
    classes = {
        traffic_class.name: {
            "throughput_mbps": float(traffic_class.value),
            "p50_ms": float(traffic_class.value * 10),
            "p95_ms": float(traffic_class.value * 20),
            "p99_ms": human_rtt_s * 1_000
            if traffic_class.name == "HUMAN_INTERACTIVE"
            else float(traffic_class.value * 30),
        }
        for traffic_class in TRAINING_LABELS
    }
    summary = {
        "system": system,
        "link_mbps": 20,
        "agent_share_pct": agent_share_pct,
        "burst_intensity": burst_intensity,
        "duration_s": 2,
        "seed": seed,
        "measurement_start_s": 100.0,
        "measurement_end_s": 102.0,
        "latency_method": "matched_two_tap",
        "latency_match": {
            "eligible_packets": 6,
            "matched_packets": 6,
            "coverage": 1.0,
            "classes": {
                traffic_class.name: {
                    "eligible_packets": 2,
                    "matched_packets": 2,
                    "coverage": 1.0,
                }
                for traffic_class in TRAINING_LABELS
            },
        },
        "generated_at": "1970-01-01T00:01:42+00:00",
        "classes": classes,
        "tool_completion": {"count": 2, "p50_ms": 2.0, "p95_ms": 3.0, "p99_ms": 4.0},
        "agent_attempts": {"attempted": 10, "completed": 8, "failed": 2},
        "corpus": {"verification_rate": 1.0},
    }
    summary_path = run_dir / "run_summary.json"
    summary_path.write_text(json.dumps(summary, sort_keys=True) + "\n", encoding="utf-8")
    summary_digest = hashlib.sha256(summary_path.read_bytes()).hexdigest()
    packet_telemetry_path = run_dir / "packet_telemetry.tsv"
    packet_telemetry_path.write_text(
        _telemetry_row(100.5, "10.0.0.2", 5201, "0x10", 100, "9.0")
        + _telemetry_row(100.6, "10.0.0.1", 8080, "0x20", 110, "9.0")
        + _telemetry_row(100.7, "10.0.0.6", 8080, "0x30", 120, "9.0")
        + _telemetry_row(101.5, "10.0.0.2", 5201, "0x11", 200, "9.0")
        + _telemetry_row(101.6, "10.0.0.1", 8080, "0x21", 210, "9.0")
        + _telemetry_row(101.7, "10.0.0.6", 8080, "0x31", 220, "9.0"),
        encoding="utf-8",
    )
    ingress_telemetry_path = run_dir / "ingress_packet_telemetry.tsv"
    ingress_telemetry_path.write_text(
        _telemetry_row(100.5 - human_rtt_s, "10.0.0.2", 5201, "0x10", 100, "")
        + _telemetry_row(100.58, "10.0.0.1", 8080, "0x20", 110, "")
        + _telemetry_row(100.68, "10.0.0.6", 8080, "0x30", 120, "")
        + _telemetry_row(101.5 - human_rtt_s * 2, "10.0.0.2", 5201, "0x11", 200, "")
        + _telemetry_row(101.58, "10.0.0.1", 8080, "0x21", 210, "")
        + _telemetry_row(101.68, "10.0.0.6", 8080, "0x31", 220, ""),
        encoding="utf-8",
    )
    manifest = {
        "status": "complete",
        "name": f"{system}_grid",
        "system": system,
        "topology": "choke_v1",
        "link_mbps": 20,
        "agent_share_pct": agent_share_pct,
        "burst_intensity": burst_intensity,
        "duration_s": 2,
        "seed": seed,
        "config_hash": config_hash,
        "output_dir": str(run_dir),
        "run_summary_sha256": summary_digest,
        "packet_telemetry_sha256": hashlib.sha256(packet_telemetry_path.read_bytes()).hexdigest(),
        "ingress_packet_telemetry_sha256": hashlib.sha256(
            ingress_telemetry_path.read_bytes()
        ).hexdigest(),
    }
    if system == "ours":
        lifecycle_path = run_dir / "controller_lifecycle.json"
        lifecycle_path.write_text(
            json.dumps(
                {
                    "ready": True,
                    "status": "stopped",
                    "workload_succeeded": True,
                    "policy_version": 1,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        manifest["controller_lifecycle_sha256"] = hashlib.sha256(
            lifecycle_path.read_bytes()
        ).hexdigest()
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
    )
    return run_dir


def _telemetry_row(
    timestamp_s: float,
    source_ip: str,
    destination_port: int,
    ip_id: str,
    sequence: int,
    ack_rtt: str,
) -> str:
    return (
        f"{timestamp_s}\t{source_ip}\t10.0.0.100\t100\t{ack_rtt}\t{ip_id}"
        f"\t6\t5000\t{destination_port}\t{sequence}\t1\t46\t0x18\n"
    )


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as input_file:
        return list(csv.DictReader(input_file))
