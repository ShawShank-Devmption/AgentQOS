"""Tests for P4.4 collision-rate reporting."""

import json
from pathlib import Path

import pytest

from eval.state_scaling import parse_points, summarize_points, write_summary


def test_summarize_points_calculates_collision_rate() -> None:
    points = parse_points(
        [
            {
                "concurrent_flows": 1_000,
                "collision_count": 2,
                "flow_observations": 1_000,
                "accuracy": 0.98,
                "register_bytes": 1_048_576,
            },
            {
                "concurrent_flows": 100_000,
                "collision_count": 400,
                "flow_observations": 100_000,
                "accuracy": 0.91,
                "register_bytes": 1_048_576,
            },
        ]
    )

    summaries = summarize_points(points)

    assert summaries[0].collision_rate == pytest.approx(0.002)
    assert summaries[1].collision_rate == pytest.approx(0.004)


def test_parse_points_rejects_nonincreasing_flow_counts() -> None:
    records = [
        {
            "concurrent_flows": 1_000,
            "collision_count": 0,
            "flow_observations": 1_000,
            "accuracy": 1.0,
            "register_bytes": 1,
        },
        {
            "concurrent_flows": 1_000,
            "collision_count": 0,
            "flow_observations": 1_000,
            "accuracy": 1.0,
            "register_bytes": 1,
        },
    ]

    with pytest.raises(ValueError, match="strictly increasing"):
        parse_points(records)


def test_write_summary_writes_derived_results(tmp_path: Path) -> None:
    input_path = tmp_path / "samples.json"
    output_path = tmp_path / "summary.json"
    input_path.write_text(
        json.dumps(
            [
                {
                    "concurrent_flows": 1_000,
                    "collision_count": 1,
                    "flow_observations": 1_000,
                    "accuracy": 0.99,
                    "register_bytes": 256,
                }
            ]
        ),
        encoding="utf-8",
    )

    write_summary(input_path, output_path)

    assert json.loads(output_path.read_text(encoding="utf-8")) == [
        {
            "accuracy": 0.99,
            "collision_rate": 0.001,
            "concurrent_flows": 1_000,
            "register_bytes": 256,
        }
    ]
