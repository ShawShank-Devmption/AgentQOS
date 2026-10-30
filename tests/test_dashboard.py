"""Host dashboard boundaries using hand-checked observations, not performance evidence."""

import json
import threading
from collections.abc import Iterator
from http.client import HTTPConnection
from pathlib import Path
from typing import Any

import pytest

from common.contracts import TrafficClass
from dashboard import server
from eval.metrics import summarize_observations


def observations() -> dict[str, Any]:
    return {
        "measurement_start_s": 0,
        "measurement_end_s": 2,
        "packets": [
            {
                "packet_id": "fixture-packet",
                "traffic_class": int(TrafficClass.HUMAN_INTERACTIVE),
                "sent_s": 0,
                "received_s": 0.02,
                "size_bytes": 1000,
            }
        ],
        "tool_calls": [
            {"call_id": "finished", "start_s": 0.1, "end_s": 0.2},
            {"call_id": "unfinished", "start_s": 0.3, "end_s": None},
        ],
        "classifications": [],
    }


@pytest.fixture
def source(tmp_path: Path) -> Path:
    path = tmp_path / "private-source.json"
    path.write_text(json.dumps(observations()), encoding="utf-8")
    return path


@pytest.mark.parametrize("as_summary", [False, True])
def test_api_uses_validated_measurements_without_modifying_source(
    source: Path, as_summary: bool
) -> None:
    if as_summary:
        source.write_text(json.dumps(summarize_observations(observations())), encoding="utf-8")
    original = source.read_bytes()
    response = server.response_for("/api/metrics", source)
    payload = json.loads(response.body)
    assert response.status == 200
    assert set(payload) == {
        "status",
        "message",
        "checked_at",
        "source_updated_at",
        "class_order",
        "summary",
    }
    assert payload["status"] == "ready"
    assert payload["class_order"] == [label.name for label in TrafficClass]
    assert payload["source_updated_at"] is not None
    summary = payload["summary"]
    human = summary["classes"][TrafficClass.HUMAN_INTERACTIVE.name]
    assert human["latency_ms"] == pytest.approx({"p50": 20, "p95": 20, "p99": 20})
    assert human["throughput_mbps"] == pytest.approx(0.004)
    assert summary["classes"][TrafficClass.UNKNOWN.name]["latency_ms"] is None
    assert summary["tool_calls"]["completed"] == summary["tool_calls"]["incomplete"] == 1
    assert source.read_bytes() == original
    assert str(source).encode() not in response.body


@pytest.mark.parametrize("content", [None, "not JSON", "{}", '{"packets": []}'])
def test_missing_invalid_or_empty_source_never_exposes_stale_success(
    source: Path, content: str | None
) -> None:
    assert server.response_for("/api/metrics", source).status == 200
    if content is None:
        source.unlink()
    else:
        source.write_text(content, encoding="utf-8")
    response = server.response_for("/api/metrics", source)
    payload = json.loads(response.body)
    assert response.status == 503
    assert payload["status"] == "unavailable"
    assert payload["summary"] is None
    assert (payload["source_updated_at"] is None) == (content is None)
    assert str(source).encode() not in response.body
    assert source.name.encode() not in response.body


def test_changed_source_is_not_presented_as_a_consistent_snapshot(
    source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def replace_during_read(path: Path) -> dict[str, Any]:
        path.write_text("{}", encoding="utf-8")
        return summarize_observations(observations())

    monkeypatch.setattr(server, "load_plot_summary", replace_during_read)
    response = server.response_for("/api/metrics", source)
    assert response.status == 503
    assert json.loads(response.body)["summary"] is None


@pytest.mark.parametrize(
    "path",
    [
        "/../AGENTS.md",
        "/%2e%2e/AGENTS.md",
        "/private-source.json",
        "/app.js/../server.py",
        "/api/metrics?source=/etc/passwd",
        "//etc/passwd",
        "/server.py",
    ],
)
def test_path_allowlist_cannot_expose_files_or_choose_source(source: Path, path: str) -> None:
    response = server.response_for(path, source)
    assert response.status == 404
    assert b"fixture-packet" not in response.body
    assert str(source).encode() not in response.body


def test_static_ui_has_accessible_status_and_safe_local_rendering(source: Path) -> None:
    html = server.response_for("/", source).body.decode()
    script = server.response_for("/app.js", source).body.decode()
    assert 'role="status"' in html
    assert "<caption>" in html
    assert 'scope="col"' in html
    assert "textContent" in script
    assert "innerHTML" not in script
    assert "AbortController" in script
    assert "source_updated_at" in script
    assert "http://" not in html and "https://" not in html
    assert server.response_for("/style.css", source).status == 200


@pytest.fixture
def live_server(source: Path) -> Iterator[server.DashboardServer]:
    with server.DashboardServer(("127.0.0.1", 0), source) as httpd:
        worker = threading.Thread(target=httpd.serve_forever, daemon=True)
        worker.start()
        try:
            yield httpd
        finally:
            httpd.shutdown()
            worker.join(timeout=2)


def test_real_http_is_read_only_uncached_and_without_cors(
    live_server: server.DashboardServer, source: Path
) -> None:
    client = HTTPConnection(*live_server.server_address, timeout=3)
    try:
        client.request("GET", "/api/metrics")
        response = client.getresponse()
        assert response.status == 200
        assert response.getheader("Cache-Control") == "no-store"
        assert response.getheader("Access-Control-Allow-Origin") is None
        assert response.getheader("X-Content-Type-Options") == "nosniff"
        assert json.loads(response.read())["status"] == "ready"
        source.write_text("{}", encoding="utf-8")
        client.request("GET", "/api/metrics")
        response = client.getresponse()
        assert response.status == 503
        assert json.loads(response.read())["summary"] is None
        client.request("POST", "/api/metrics", body="overwrite")
        response = client.getresponse()
        assert response.status == 501
        response.read()
        assert source.read_text() == "{}"
    finally:
        client.close()
