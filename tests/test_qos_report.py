"""D4 evidence cannot pass from absent, late, lost, or mismatched measurements."""

import hashlib
import json
from pathlib import Path

import pytest

from eval.qos_report import declare_target, qos_report
from tests.test_overhead_report import _write_run


def _evidence(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    target = tmp_path / "target.json"
    target.write_text('{"human_p99_target_ms": 20, "declared_at": "2026-10-31T00:00:00+00:00"}')
    ours = _write_run(tmp_path, "ours", 10)
    fifo = _write_run(tmp_path, "fifo", 30)
    for path in (ours, fifo):
        metadata = json.loads((path / "started.json").read_text())
        metadata.update(
            started_at="2026-10-31T01:00:00+00:00", agent_share_pct=30, burst_intensity="low"
        )
        metadata["config"].update(topology="choke_v1", link_mbps=20, duration_s=30)
        (path / "started.json").write_text(json.dumps(metadata))
    raw = json.loads((ours / "observations.json").read_text())
    raw["classifications"] = [
        {
            "flow_id": f"flow-{label}",
            "checkpoint": 16,
            "truth": label,
            "predicted": label,
            "label_source": "orchestration",
        }
        for label in (1, 3)
    ]
    _observations(ours, raw)
    ptf = tmp_path / "ptf.log"
    ptf.write_text("""D4 real_queue congestion_flag=1 HI=64 LO=16
D4 real_queue congestion_flag=0 HI=64 LO=16
D4 isolated_selection preset=1 forwarded=0
D4 isolated_selection preset=0 forwarded=1
D4 isolated_red class=UNKNOWN ecn=0 forwarded=1 meter_drops=0
D4 isolated_red class=HUMAN_INTERACTIVE ecn=0 forwarded=1 meter_drops=0
D4 isolated_red class=AGENT_INTERACTIVE ecn=0 forwarded=1 meter_drops=0
D4 isolated_red class=AGENT_INTERACTIVE ecn=2 forwarded=1 meter_drops=0
D4 isolated_red class=AGENT_BULK ecn=0 forwarded=0 meter_drops=1
D4 isolated_red class=AGENT_BULK ecn=2 forwarded=1 meter_drops=0
OK
""")
    return target, ours, fifo, ptf


def _observations(path: Path, raw: dict[str, object]) -> None:
    encoded = json.dumps(raw).encode()
    (path / "observations.json").write_bytes(encoded)
    (path / "completed.json").write_text(
        json.dumps(
            {
                "status": "success",
                "observations_sha256": hashlib.sha256(encoded).hexdigest(),
            }
        )
    )


def test_matched_complete_evidence_reports_latency_and_test_scope(tmp_path: Path) -> None:
    report = qos_report(*_evidence(tmp_path))
    assert report["evidence_pass"] is True
    assert report["human_p99_ratio"] == pytest.approx(1 / 3)
    assert "not live workload drop counters" in report["note"]


@pytest.mark.parametrize("missing", ["ptf", "labels", "latency", "selection", "drops", "loss"])
def test_missing_or_failing_evidence_never_passes(tmp_path: Path, missing: str) -> None:
    target, ours, fifo, ptf = _evidence(tmp_path)
    if missing in {"ptf", "selection", "drops"}:
        text = ptf.read_text()
        if missing == "ptf":
            text = text.replace("OK", "FAILED")
        elif missing == "selection":
            text = text.replace("D4 isolated_selection", "not-observed")
        else:
            text = text.replace(
                "class=AGENT_INTERACTIVE ecn=0 forwarded=1 meter_drops=0",
                "class=AGENT_INTERACTIVE ecn=0 forwarded=0 meter_drops=1",
            )
        ptf.write_text(text)
    else:
        raw = json.loads((ours / "observations.json").read_text())
        if missing == "labels":
            raw["classifications"] = []
        elif missing == "loss":
            raw["packets"][0]["received_s"] = None
        else:
            for packet in raw["packets"]:
                packet["received_s"] = None
        _observations(ours, raw)
    assert qos_report(target, ours, fifo, ptf)["evidence_pass"] is False


def test_late_target_or_different_seed_is_rejected(tmp_path: Path) -> None:
    target, ours, fifo, ptf = _evidence(tmp_path)
    data = json.loads(target.read_text())
    data["declared_at"] = "2026-11-01T00:00:00+00:00"
    target.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="before both"):
        qos_report(target, ours, fifo, ptf)


def test_target_is_positive_and_append_only(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    for invalid in (0, -1, float("nan"), float("inf"), True):
        with pytest.raises(ValueError):
            declare_target(target, invalid)
    declare_target(target, 20)
    with pytest.raises(FileExistsError):
        declare_target(target, 30)
