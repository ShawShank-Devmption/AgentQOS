"""Hand-checked tests for evaluation metric math and readers."""

import json
from pathlib import Path

import pytest

from common.contracts import TrafficClass
from eval.metrics import (
    MetricInputError,
    classification_metrics,
    completion_times,
    confidence_interval_95,
    evaluate_anchors,
    percentile_summary,
    read_latency_csv,
)


def test_percentiles_use_linear_interpolation() -> None:
    summary = percentile_summary((1.0, 2.0, 3.0, 4.0, 5.0))

    assert summary.count == 5
    assert summary.p50_ms == 3.0
    assert summary.p95_ms == pytest.approx(4.8)
    assert summary.p99_ms == pytest.approx(4.96)


def test_completion_times_pair_request_start_and_end_and_skip_errors(tmp_path: Path) -> None:
    log_path = tmp_path / "requests.jsonl"
    entries = [
        {"start_time_ns": 1_000_000_000, "end_time_ns": 1_250_000_000, "status": "ok"},
        {"start_time_ns": 2_000_000_000, "end_time_ns": 2_010_000_000, "status": "error"},
        {"start_time_ns": 3_000_000_000, "end_time_ns": 3_500_000_000, "status": "ok"},
    ]
    log_path.write_text(
        "".join(json.dumps(entry) + "\n" for entry in entries),
        encoding="utf-8",
    )

    assert completion_times(log_path) == (250.0, 500.0)


def test_classification_metrics_report_per_class_confusion_counts() -> None:
    scores = classification_metrics(
        (
            TrafficClass.HUMAN_INTERACTIVE,
            TrafficClass.HUMAN_INTERACTIVE,
            TrafficClass.AGENT_INTERACTIVE,
            TrafficClass.AGENT_BULK,
        ),
        (
            TrafficClass.HUMAN_INTERACTIVE,
            TrafficClass.AGENT_INTERACTIVE,
            TrafficClass.AGENT_INTERACTIVE,
            TrafficClass.UNKNOWN,
        ),
    )

    human = scores[TrafficClass.HUMAN_INTERACTIVE]
    assert (human.true_positive, human.false_positive, human.false_negative) == (1, 0, 1)
    assert human.precision == 1.0
    assert human.recall == 0.5
    bulk = scores[TrafficClass.AGENT_BULK]
    assert (bulk.true_positive, bulk.false_positive, bulk.false_negative) == (0, 0, 1)
    assert bulk.precision is None
    assert bulk.recall == 0.0


def test_classification_metrics_marks_absent_class_as_undefined() -> None:
    scores = classification_metrics(
        (TrafficClass.HUMAN_INTERACTIVE,),
        (TrafficClass.HUMAN_INTERACTIVE,),
    )

    absent = scores[TrafficClass.AGENT_INTERACTIVE]
    assert absent.precision is None
    assert absent.recall is None


def test_confidence_interval_uses_student_t_for_five_seeds() -> None:
    interval = confidence_interval_95((1.0, 2.0, 3.0, 4.0, 5.0))

    assert interval.sample_count == 5
    assert interval.mean == 3.0
    assert interval.low == pytest.approx(1.0367568)
    assert interval.high == pytest.approx(4.9632432)


def test_anchor_checks_report_reduction_and_overhead() -> None:
    result = evaluate_anchors(
        ours_p99_ms=60.0,
        baseline_p99_ms=100.0,
        full_pipeline_latency_ms=1.04,
        minimal_pipeline_latency_ms=1.0,
        min_p99_reduction=0.30,
        max_overhead=0.05,
    )

    assert result.p99_reduction == pytest.approx(0.40)
    assert result.pipeline_overhead == pytest.approx(0.04)
    assert result.p99_passed
    assert result.overhead_passed


def test_latency_reader_rejects_missing_or_non_numeric_values(tmp_path: Path) -> None:
    missing = tmp_path / "missing-column.csv"
    missing.write_text("delay_ms\n1.0\n", encoding="utf-8")
    invalid = tmp_path / "invalid.csv"
    invalid.write_text("latency_ms\nnot-a-number\n", encoding="utf-8")

    with pytest.raises(MetricInputError, match="latency_ms"):
        read_latency_csv(missing)
    with pytest.raises(MetricInputError, match="line 2"):
        read_latency_csv(invalid)
