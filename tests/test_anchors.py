"""Tests for traceable headline-anchor evaluation."""

import json
from pathlib import Path

import pytest

from eval.anchors import AnchorInputError, evaluate_anchor_files, main


def _write_inputs(root: Path) -> tuple[Path, Path]:
    centerpiece = root / "centerpiece.csv"
    centerpiece.write_text(
        "time_s,system,human_p99_ms\n"
        "1,ours,60\n"
        "2,ours,80\n"
        "1,fifo,100\n"
        "2,fifo,100\n"
        "1,diffserv,110\n"
        "2,diffserv,110\n"
        "1,fairq,120\n"
        "2,fairq,120\n"
        "1,app_limiter,130\n"
        "2,app_limiter,130\n",
        encoding="utf-8",
    )
    overhead = root / "overhead.csv"
    overhead.write_text(
        "pipeline,latency_ms\nminimal,1.0\nminimal,1.0\nfull,1.04\nfull,1.04\n",
        encoding="utf-8",
    )
    return centerpiece, overhead


def test_anchor_report_selects_strictest_baseline_and_attests_inputs(tmp_path: Path) -> None:
    centerpiece, overhead = _write_inputs(tmp_path)
    output = tmp_path / "anchors.json"

    report = evaluate_anchor_files(
        centerpiece,
        overhead,
        output,
        min_p99_reduction=0.30,
        max_overhead=0.05,
    )

    assert report.best_baseline == "fifo"
    assert report.ours_p99_ms == pytest.approx(70.0)
    assert report.baseline_p99_ms == pytest.approx(100.0)
    assert report.result.p99_passed
    assert report.result.overhead_passed
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["passed"] is True
    assert len(payload["inputs"]["centerpiece_sha256"]) == 64
    assert len(payload["inputs"]["overhead_sha256"]) == 64


def test_anchor_report_writes_failed_outcome_without_hiding_it(tmp_path: Path) -> None:
    centerpiece, overhead = _write_inputs(tmp_path)
    output = tmp_path / "anchors.json"

    report = evaluate_anchor_files(
        centerpiece,
        overhead,
        output,
        min_p99_reduction=0.40,
        max_overhead=0.03,
    )

    assert not report.result.p99_passed
    assert not report.result.overhead_passed
    assert json.loads(output.read_text(encoding="utf-8"))["passed"] is False


def test_anchor_cli_returns_distinct_failure_status_after_writing_report(tmp_path: Path) -> None:
    centerpiece, overhead = _write_inputs(tmp_path)
    output = tmp_path / "anchors.json"

    status = main(
        [
            str(centerpiece),
            str(overhead),
            str(output),
            "--min-p99-reduction",
            "0.40",
            "--max-overhead",
            "0.03",
        ]
    )

    assert status == 2
    assert output.is_file()


def test_anchor_report_rejects_unbalanced_system_timelines(tmp_path: Path) -> None:
    centerpiece, overhead = _write_inputs(tmp_path)
    rows = centerpiece.read_text(encoding="utf-8").splitlines()
    centerpiece.write_text("\n".join(row for row in rows if row != "2,fifo,100") + "\n")

    with pytest.raises(AnchorInputError, match="same time coordinates"):
        evaluate_anchor_files(
            centerpiece,
            overhead,
            tmp_path / "anchors.json",
            min_p99_reduction=0.30,
            max_overhead=0.05,
        )
