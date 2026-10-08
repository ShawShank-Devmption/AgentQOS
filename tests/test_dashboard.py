"""Tests for the read-only live metrics dashboard."""

import json
import threading
import urllib.request
from pathlib import Path

import pytest

from dashboard.producer import LiveMetricError, build_snapshot, write_snapshot
from dashboard.server import SnapshotError, create_server, read_snapshot

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _snapshot() -> dict[str, object]:
    return {
        "generated_at": "2026-10-06T12:00:00+00:00",
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
