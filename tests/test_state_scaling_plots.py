"""P4.4 plotting uses complete seed sets and never hides failed concurrency points."""

import hashlib
import json
from pathlib import Path

import pytest

from eval.state_scaling_plots import aggregate_scaling, render_scaling_figure
from tests.test_overhead_report import _write_run
from tests.test_state_scaling import _profile


def _sweep(tmp_path: Path) -> tuple[list[Path], Path]:
    profile = _profile(tmp_path)
    paths = []
    for seed in range(1, 6):
        path = _write_run(tmp_path / f"seed-{seed}", "ours", 10)
        (path / "inputs/profile.json").write_bytes(profile.read_bytes())
        hashes = json.loads((path / "inputs/sha256.json").read_text())
        hashes["profile"] = hashlib.sha256(profile.read_bytes()).hexdigest()
        (path / "inputs/sha256.json").write_text(json.dumps(hashes))
        start = json.loads((path / "started.json").read_text())
        start.update(seed=seed, agent_share_pct=50, burst_intensity="low")
        start["config"].update(topology="choke_v1", link_mbps=50, duration_s=30)
        (path / "started.json").write_text(json.dumps(start))
        (path / "analysis").mkdir()
        (path / "analysis/scaling-report.json").write_text(
            json.dumps(
                {
                    "provenance": "real_switch_captures_synthetic_stress_workload",
                    "seed": seed,
                    "requested_flows": 4,
                    "requested_concurrency_observed": True,
                    "collision_events_per_ingress_packet": 0.1,
                    "switch_rss_bytes": 1000 + seed,
                    "conditions": {"switch_cpu": 0, "switch_binary_sha256": "test-only"},
                    "footprint": {"register_bits": 8192},
                    "accuracy": {"6": {"correct_fraction_all_offered": 0.5}},
                }
            )
        )
        paths.append(path)
    return paths, profile


def test_complete_sweep_aggregates_seeds_and_renders_standalone_figure(tmp_path: Path) -> None:
    paths, profile = _sweep(tmp_path)
    rows = aggregate_scaling(paths, Path("eval/configs/state_scaling_1000.yaml"), profile)
    assert rows[0]["accuracy_all_offered"]["mean"] == 0.5
    assert rows[0]["switch_rss_bytes"]["samples"] == 5
    render_scaling_figure(rows, tmp_path / "figure")
    assert (tmp_path / "figure/fig5.pdf").read_bytes().startswith(b"%PDF")
    assert (tmp_path / "figure/fig5.png").stat().st_size > 0


def test_missing_seed_is_rejected_and_unmet_concurrency_is_unavailable(tmp_path: Path) -> None:
    paths, profile = _sweep(tmp_path)
    config = Path("eval/configs/state_scaling_1000.yaml")
    with pytest.raises(ValueError, match="missing"):
        aggregate_scaling(paths[:-1], config, profile)
    report_path = paths[0] / "analysis/scaling-report.json"
    data = json.loads(report_path.read_text())
    data["requested_concurrency_observed"] = False
    report_path.write_text(json.dumps(data))
    rows = aggregate_scaling(paths, config, profile)
    assert rows[0]["accuracy_all_offered"] is None
    assert rows[0]["switch_rss_bytes"] is None


def test_analytical_plan_cannot_be_passed_off_as_live_evidence(tmp_path: Path) -> None:
    paths, profile = _sweep(tmp_path)
    path = paths[0] / "analysis/scaling-report.json"
    data = json.loads(path.read_text())
    data["provenance"] = "analytical_hash_plan_not_switch_measurement"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="analytical"):
        aggregate_scaling(paths, Path("eval/configs/state_scaling_1000.yaml"), profile)
