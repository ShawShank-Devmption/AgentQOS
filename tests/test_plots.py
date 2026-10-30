"""Plotting verifies actual deterministic figure artifacts from observations."""

import hashlib
import json
from pathlib import Path

from eval import plots


def test_plots_are_deterministic_and_keep_source_unchanged(tmp_path: Path) -> None:
    raw = {
        "measurement_start_s": 0,
        "measurement_end_s": 1,
        "packets": [
            {
                "packet_id": "one",
                "traffic_class": 1,
                "sent_s": 0,
                "received_s": 0.02,
                "size_bytes": 100,
            }
        ],
        "tool_calls": [],
        "classifications": [],
    }
    source = tmp_path / "observations.json"
    source.write_text(json.dumps(raw), encoding="utf-8")
    before = source.read_bytes()
    first = plots.render_figures(source, tmp_path / "one")
    second = plots.render_figures(source, tmp_path / "two")
    assert {path.name for path in first} == {
        "latency.png",
        "throughput.png",
        "tool_completion.png",
        "classification.png",
    }
    for left, right in zip(first, second, strict=True):
        assert left.read_bytes().startswith(b"\x89PNG")
        assert (
            hashlib.sha256(left.read_bytes()).digest()
            == hashlib.sha256(right.read_bytes()).digest()
        )
    assert source.read_bytes() == before
    assert plots.main([str(source), "--output-dir", str(tmp_path / "cli")]) == 0


def test_summary_plots_and_missing_data_validation(tmp_path: Path) -> None:
    import pytest

    from eval.metrics import MeasurementError, summarize_observations

    raw = {
        "measurement_start_s": 0,
        "measurement_end_s": 1,
        "packets": [
            {
                "packet_id": "one",
                "traffic_class": 1,
                "sent_s": 0,
                "received_s": 0.02,
                "size_bytes": 100,
            }
        ],
        "tool_calls": [],
        "classifications": [],
    }
    source = tmp_path / "summary.json"
    summary = summarize_observations(raw)
    source.write_text(json.dumps(summary), encoding="utf-8")
    assert len(plots.render_figures(source, tmp_path / "valid")) == 4
    summary["classes"]["HUMAN_INTERACTIVE"]["latency_ms"]["p99"] = float("nan")
    source.write_text(json.dumps(summary), encoding="utf-8")
    with pytest.raises(MeasurementError):
        plots.render_figures(source, tmp_path / "invalid")
    assert not (tmp_path / "invalid").exists()
