"""C4 report refuses unpaired, changed, or incomplete experiment evidence."""

import hashlib
import json
from pathlib import Path

import pytest

from eval.overhead_report import compare_runs, markdown_report
from eval.workload import load_profile


def _write_run(root: Path, system: str, latency_ms: float, *, lost: bool = False) -> Path:
    path = root / system
    (path / "inputs").mkdir(parents=True)
    hashes = {}
    for name, value in {
        "profile": load_profile(Path("eval/configs/workload_runtime.json")),
        "pipeline": {"system": system},
        **({"model": {}} if system == "ours" else {}),
    }.items():
        raw = json.dumps(value).encode()
        (path / "inputs" / f"{name}.json").write_bytes(raw)
        hashes[name] = hashlib.sha256(raw).hexdigest()
    (path / "inputs/sha256.json").write_text(json.dumps(hashes))
    (path / "started.json").write_text(
        json.dumps(
            {
                "host": "same-host",
                "seed": 1,
                "config": {"system": system},
            }
        )
    )
    offsets = [index / 100 for index in range(2980)]
    (path / "workload-plan.json").write_text(
        json.dumps(
            {
                "seed": 1,
                "duration_s": 30,
                "flows": [{"offsets_s": offsets}],
            }
        )
    )
    raw = {
        "measurement_start_s": 10,
        "measurement_end_s": 40,
        "packets": [
            {
                "packet_id": f"p{index}",
                "traffic_class": 1,
                "sent_s": 10 + offset,
                "received_s": None if lost else 10 + offset + latency_ms / 1000,
                "size_bytes": 100,
            }
            for index, offset in enumerate(offsets)
        ],
        "tool_calls": [],
        "classifications": [],
    }
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
    (path / "overhead.json").write_text(
        json.dumps(
            {
                "system": system,
                "requested_pps": 100,
                "switch_cpu_pct": 25,
                "switch_cpu": 0,
                "switch_binary_sha256": "same-binary",
                "footprint": {},
                "cpu_port_tx_packets": 2 if system == "ours" else None,
                "cpu_port_tx_pps": 0.1 if system == "ours" else None,
                "punt_cap_exceeded": False if system == "ours" else None,
            }
        )
    )
    return path


def test_report_calculates_relative_overhead_and_human_ratio(tmp_path: Path) -> None:
    fifo = _write_run(tmp_path, "fifo", 10)
    ours = _write_run(tmp_path, "ours", 10.4)
    report = compare_runs([fifo, ours])
    pair = report["pairs"][0]
    assert pair["added_p99_fraction"] == pytest.approx(0.04)
    assert pair["human_p99_ratio"] == pytest.approx(1.04)
    assert report["highest_tested_loss_free_passing_pps"] == 100
    assert "unavailable" in markdown_report(report)  # FIFO has no CPU port.


@pytest.mark.parametrize(
    "change", ["schedule", "cpu", "binary", "failed", "corrupt", "input", "host"]
)
def test_report_rejects_incomparable_or_changed_evidence(tmp_path: Path, change: str) -> None:
    fifo = _write_run(tmp_path, "fifo", 10)
    ours = _write_run(tmp_path, "ours", 10.1)
    if change == "schedule":
        (ours / "workload-plan.json").write_text('{"seed": 1, "duration_s": 31}')
    elif change in {"cpu", "binary"}:
        path = ours / "overhead.json"
        data = json.loads(path.read_text())
        data["switch_cpu" if change == "cpu" else "switch_binary_sha256"] = 2
        path.write_text(json.dumps(data))
    elif change == "failed":
        (ours / "completed.json").write_text('{"status": "failed"}')
    elif change == "input":
        (ours / "inputs/profile.json").write_text("{}")
    elif change == "host":
        (ours / "started.json").write_text(
            json.dumps(
                {
                    "host": "another-host",
                    "seed": 1,
                    "config": {"system": "ours"},
                }
            )
        )
    else:
        (ours / "observations.json").write_text("{}")
    with pytest.raises(ValueError):
        compare_runs([fifo, ours])


def test_loss_or_missing_samples_cannot_produce_a_safe_rate(tmp_path: Path) -> None:
    fifo = _write_run(tmp_path, "fifo", 10)
    ours = _write_run(tmp_path, "ours", 10, lost=True)
    report = compare_runs([fifo, ours])
    assert report["highest_tested_loss_free_passing_pps"] is None
    assert report["pairs"][0]["latency_anchor_pass"] is None


def test_report_requires_exactly_one_partner_per_system(tmp_path: Path) -> None:
    fifo = _write_run(tmp_path, "fifo", 10)
    with pytest.raises(ValueError, match="partner"):
        compare_runs([fifo])
    with pytest.raises(ValueError, match="duplicate"):
        compare_runs([fifo, fifo])
