"""Tests for the read-only live metrics dashboard."""

import json
import threading
import urllib.request
from pathlib import Path

import pytest

from dashboard.producer import LiveMetricError, build_snapshot, read_telemetry, write_snapshot
from dashboard.server import SnapshotError, create_server, read_snapshot

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _snapshot() -> dict[str, object]:
    return {
        "generated_at": "2026-10-06T12:00:00+00:00",
        "latency_method": "matched_two_tap",
        "latency_match": {
            "eligible_packets": 10,
            "matched_packets": 9,
            "coverage": 0.9,
            "classes": {
                "HUMAN_INTERACTIVE": {
                    "eligible_packets": 4,
                    "matched_packets": 4,
                    "coverage": 1.0,
                },
                "AGENT_INTERACTIVE": {
                    "eligible_packets": 3,
                    "matched_packets": 3,
                    "coverage": 1.0,
                },
                "AGENT_BULK": {
                    "eligible_packets": 3,
                    "matched_packets": 2,
                    "coverage": 2 / 3,
                },
            },
        },
        "classes": {
            "HUMAN_INTERACTIVE": {
                "throughput_mbps": 4.5,
                "p50_ms": 10.0,
                "p95_ms": 20.0,
                "p99_ms": 30.0,
            },
            "AGENT_INTERACTIVE": {
                "throughput_mbps": 3.0,
                "p50_ms": 12.0,
                "p95_ms": 25.0,
                "p99_ms": 35.0,
            },
            "AGENT_BULK": {
                "throughput_mbps": 1.0,
                "p50_ms": 50.0,
                "p95_ms": 80.0,
                "p99_ms": 100.0,
            },
        },
    }


def test_snapshot_reader_validates_all_live_metrics(tmp_path: Path) -> None:
    path = tmp_path / "snapshot.json"
    snapshot = _snapshot()
    path.write_text(json.dumps(snapshot), encoding="utf-8")

    assert read_snapshot(path) == snapshot


def test_snapshot_reader_rejects_negative_values(tmp_path: Path) -> None:
    path = tmp_path / "snapshot.json"
    snapshot = _snapshot()
    classes = snapshot["classes"]
    assert isinstance(classes, dict)
    human = classes["HUMAN_INTERACTIVE"]
    assert isinstance(human, dict)
    human["p99_ms"] = -1
    path.write_text(json.dumps(snapshot), encoding="utf-8")

    with pytest.raises(SnapshotError, match="non-negative"):
        read_snapshot(path)


def test_snapshot_reader_rejects_inconsistent_match_coverage(tmp_path: Path) -> None:
    path = tmp_path / "snapshot.json"
    snapshot = _snapshot()
    latency_match = snapshot["latency_match"]
    assert isinstance(latency_match, dict)
    latency_match["coverage"] = 0.5
    path.write_text(json.dumps(snapshot), encoding="utf-8")

    with pytest.raises(SnapshotError, match="match coverage"):
        read_snapshot(path)


def test_dashboard_serves_html_and_current_snapshot(tmp_path: Path) -> None:
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(json.dumps(_snapshot()), encoding="utf-8")
    server = create_server(
        "127.0.0.1",
        0,
        snapshot_path,
        PROJECT_ROOT / "dashboard/index.html",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/", timeout=2) as response:  # noqa: S310
            html = response.read().decode("utf-8")
        with urllib.request.urlopen(  # noqa: S310
            f"http://{host}:{port}/api/metrics", timeout=2
        ) as response:
            metrics = json.load(response)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert "Agent-Aware Network" in html
    assert metrics == _snapshot()


def test_live_producer_aggregates_packet_telemetry_by_frozen_class(tmp_path: Path) -> None:
    telemetry_path = tmp_path / "packets.tsv"
    telemetry_path.write_text(
        "97.0\t10.0.0.2\t10.0.0.100\t1000\t0.010\n"
        "98.0\t10.0.0.100\t10.0.0.2\t1000\t0.020\n"
        "99.0\t10.0.0.1\t10.0.0.100\t500\t0.030\n"
        "99.5\t10.0.0.6\t10.0.0.100\t250\t0.040\n"
        "80.0\t10.0.0.2\t10.0.0.100\t9000\t0.900\n",
        encoding="utf-8",
    )

    snapshot = build_snapshot(telemetry_path, now_s=100.0, window_s=5.0)
    classes = snapshot["classes"]
    assert isinstance(classes, dict)
    human = classes["HUMAN_INTERACTIVE"]
    interactive = classes["AGENT_INTERACTIVE"]
    bulk = classes["AGENT_BULK"]
    assert human["throughput_mbps"] == pytest.approx(0.0032)
    assert human["p50_ms"] == pytest.approx(15.0)
    assert human["p95_ms"] == pytest.approx(19.5)
    assert human["p99_ms"] == pytest.approx(19.9)
    assert interactive["p50_ms"] == pytest.approx(30.0)
    assert bulk["p99_ms"] == pytest.approx(40.0)


def test_live_producer_uses_matched_two_tap_transit_latency(tmp_path: Path) -> None:
    ingress_path = tmp_path / "ingress.tsv"
    target_path = tmp_path / "target.tsv"
    ingress_path.write_text(
        "99.000\t10.0.0.2\t10.0.0.100\t1000\t\t0x0010\t6\t5000\t5201\t100\t1\t946\t0x0018\n"
        "99.100\t10.0.0.1\t10.0.0.100\t500\t\t0x0011\t6\t5001\t8080\t200\t1\t446\t0x0018\n",
        encoding="utf-8",
    )
    target_path.write_text(
        "99.010\t10.0.0.2\t10.0.0.100\t1000\t0.900\t0x0010\t6\t5000\t5201\t100\t1\t946\t0x0018\n"
        "99.130\t10.0.0.1\t10.0.0.100\t500\t0.800\t0x0011\t6\t5001\t8080\t200\t1\t446\t0x0018\n",
        encoding="utf-8",
    )

    snapshot = build_snapshot(
        target_path,
        ingress_telemetry_path=ingress_path,
        now_s=100.0,
        window_s=5.0,
    )

    classes = snapshot["classes"]
    assert isinstance(classes, dict)
    assert snapshot["latency_method"] == "matched_two_tap"
    assert snapshot["latency_match"] == {
        "eligible_packets": 2,
        "matched_packets": 2,
        "coverage": 1.0,
        "classes": {
            "HUMAN_INTERACTIVE": {
                "eligible_packets": 1,
                "matched_packets": 1,
                "coverage": 1.0,
            },
            "AGENT_INTERACTIVE": {
                "eligible_packets": 1,
                "matched_packets": 1,
                "coverage": 1.0,
            },
            "AGENT_BULK": {
                "eligible_packets": 0,
                "matched_packets": 0,
                "coverage": 0.0,
            },
        },
    }
    assert classes["HUMAN_INTERACTIVE"]["p99_ms"] == pytest.approx(10.0)
    assert classes["AGENT_INTERACTIVE"]["p99_ms"] == pytest.approx(30.0)


def test_live_producer_parses_extended_packet_identity_fields(tmp_path: Path) -> None:
    telemetry_path = tmp_path / "packets.tsv"
    telemetry_path.write_text(
        "99.000\t10.0.0.2\t10.0.0.100\t1000\t\t0x0010\t6\t5000\t5201\t100\t1\t946\t0x0018\n",
        encoding="utf-8",
    )

    packet = read_telemetry(telemetry_path)[0]

    assert packet.packet_key is not None
    assert packet.packet_key.ip_id == 16
    assert packet.packet_key.tcp_sequence == 100


def test_two_tap_latency_matches_reverse_direction_at_the_source_tap(tmp_path: Path) -> None:
    ingress_path = tmp_path / "ingress.tsv"
    target_path = tmp_path / "target.tsv"
    ingress_path.write_text(
        "99.020\t10.0.0.100\t10.0.0.2\t100\t\t0x20\t6\t5201\t5000\t1\t101\t46\t0x10\n",
        encoding="utf-8",
    )
    target_path.write_text(
        "99.000\t10.0.0.100\t10.0.0.2\t100\t8.0\t0x20\t6\t5201\t5000\t1\t101\t46\t0x10\n",
        encoding="utf-8",
    )

    snapshot = build_snapshot(
        target_path,
        ingress_telemetry_path=ingress_path,
        now_s=100.0,
        window_s=5.0,
    )

    classes = snapshot["classes"]
    assert isinstance(classes, dict)
    assert classes["HUMAN_INTERACTIVE"]["p99_ms"] == pytest.approx(20.0)


def test_snapshot_reader_rejects_class_match_totals_inconsistent_with_global(
    tmp_path: Path,
) -> None:
    path = tmp_path / "snapshot.json"
    snapshot = _snapshot()
    latency_match = snapshot["latency_match"]
    assert isinstance(latency_match, dict)
    class_matches = latency_match["classes"]
    assert isinstance(class_matches, dict)
    human = class_matches["HUMAN_INTERACTIVE"]
    assert isinstance(human, dict)
    human["matched_packets"] = 3
    human["coverage"] = 0.75
    path.write_text(json.dumps(snapshot), encoding="utf-8")

    with pytest.raises(SnapshotError, match="class match totals"):
        read_snapshot(path)


def test_live_producer_writes_a_server_valid_snapshot_atomically(tmp_path: Path) -> None:
    telemetry_path = tmp_path / "packets.tsv"
    telemetry_path.write_text("10.0\t10.0.0.2\t10.0.0.100\t100\t0.001\n", encoding="utf-8")
    snapshot_path = tmp_path / "live_metrics.json"

    write_snapshot(telemetry_path, snapshot_path, now_s=10.0, window_s=5.0)

    assert read_snapshot(snapshot_path) == json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert not (tmp_path / "live_metrics.json.tmp").exists()


def test_live_producer_rejects_malformed_complete_telemetry_rows(tmp_path: Path) -> None:
    telemetry_path = tmp_path / "packets.tsv"
    telemetry_path.write_text("10.0\t10.0.0.2\tbroken\n", encoding="utf-8")

    with pytest.raises(LiveMetricError, match="line 1"):
        build_snapshot(telemetry_path, now_s=10.0, window_s=5.0)
