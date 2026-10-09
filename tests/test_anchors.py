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
        "pair_id,seed,host_id,load_profile,sample_id,pipeline,latency_ms\n"
        + "".join(
            f"pair-{seed},{seed},linux-vm,20mbps,packet-1,minimal,1.0\n"
            f"pair-{seed},{seed},linux-vm,20mbps,packet-1,full,1.04\n"
            for seed in range(1, 6)
        ),
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
    centerpiece.write_text(
        centerpiece.read_text(encoding="utf-8").replace("1,ours,60", "1,ours,90"),
        encoding="utf-8",
    )
    overhead.write_text(
        overhead.read_text(encoding="utf-8").replace(",full,1.04", ",full,1.06"),
        encoding="utf-8",
    )

    report = evaluate_anchor_files(
        centerpiece,
        overhead,
        output,
    )

    assert not report.result.p99_passed
    assert not report.result.overhead_passed
    assert json.loads(output.read_text(encoding="utf-8"))["passed"] is False


def test_anchor_cli_returns_distinct_failure_status_after_writing_report(tmp_path: Path) -> None:
    centerpiece, overhead = _write_inputs(tmp_path)
    output = tmp_path / "anchors.json"
    centerpiece.write_text(
        centerpiece.read_text(encoding="utf-8").replace("1,ours,60", "1,ours,90"),
        encoding="utf-8",
    )

    status = main(
        [
            str(centerpiece),
            str(overhead),
            str(output),
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
        )


def test_anchor_report_rejects_unpaired_overhead_samples(tmp_path: Path) -> None:
    centerpiece, overhead = _write_inputs(tmp_path)
    rows = overhead.read_text(encoding="utf-8").splitlines()
    overhead.write_text("\n".join(rows[:-1]) + "\n", encoding="utf-8")

    with pytest.raises(AnchorInputError, match="must be paired"):
        evaluate_anchor_files(centerpiece, overhead, tmp_path / "anchors.json")
