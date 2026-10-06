"""Tests for the read-only live metrics dashboard."""

import json
import threading
import urllib.request
from pathlib import Path

import pytest

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
