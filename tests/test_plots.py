"""Tests for deterministic paper-figure generation from immutable raw inputs."""

import hashlib
from pathlib import Path

import pytest

from eval.plots import PlotInputError, generate_figures


def _write_raw_inputs(root: Path) -> None:
    root.mkdir()
    (root / "centerpiece.csv").write_text(
        "time_s,system,human_p99_ms\n"
        "0,ours,10\n10,ours,12\n0,fifo,11\n10,fifo,70\n"
        "0,diffserv,15\n0,fairq,14\n0,app_limiter,13\n",
        encoding="utf-8",
    )
    (root / "classification.csv").write_text(
        "class,precision,recall\n"
        "HUMAN_INTERACTIVE,0.9,0.8\n"
        "AGENT_INTERACTIVE,0.8,0.7\n"
        "AGENT_BULK,0.7,0.6\n",
        encoding="utf-8",
    )
    (root / "overhead.csv").write_text(
        "pair_id,seed,host_id,load_profile,sample_id,pipeline,latency_ms\n"
        "pair-1,1,linux-vm,20mbps,packet-1,minimal,1.0\n"
        "pair-1,1,linux-vm,20mbps,packet-1,full,1.1\n"
        "pair-2,2,linux-vm,20mbps,packet-2,minimal,2.0\n"
        "pair-2,2,linux-vm,20mbps,packet-2,full,2.2\n",
        encoding="utf-8",
    )
    (root / "scaling.csv").write_text(
        "concurrent_flows,accuracy,memory_bytes,collision_rate\n"
        "1000,0.95,1024,0.01\n100000,0.88,4096,0.08\n",
        encoding="utf-8",
    )
    (root / "evasion.csv").write_text(
        "think_time_ms,accuracy,throughput_tps\n0,0.9,100\n1000,0.6,10\n",
        encoding="utf-8",
    )
    (root / "feature_importance.csv").write_text(
        "feature,importance\n"
        "iat_ewma_us,0.20\n"
        "iat_var_ewma,0.15\n"
        "pkt_count,0.15\n"
        "mean_pkt_size_up,0.10\n"
        "updown_ratio_x100,0.10\n"
        "first8_size_bucket,0.08\n"
        "fanout_new_flows,0.07\n"
        "tls_ext_count,0.06\n"
        "tls_alpn_class,0.05\n"
        "flow_age_ms,0.04\n",
        encoding="utf-8",
    )


def test_generate_figures_uses_stable_required_filenames_and_preserves_raw_data(
    tmp_path: Path,
) -> None:
    raw_dir = tmp_path / "raw"
    _write_raw_inputs(raw_dir)
    before = {path.name: path.read_bytes() for path in raw_dir.iterdir()}

    outputs = generate_figures(raw_dir, tmp_path / "figures")

    assert tuple(path.name for path in outputs) == (
        "fig2_human_p99_timeline.png",
        "fig3_precision_recall.png",
        "fig4_overhead_cdf.png",
        "fig5_scaling.png",
        "fig6_evasion.png",
        "table_feature_importance.csv",
    )
    assert all(path.is_file() and path.stat().st_size > 0 for path in outputs)
    assert {path.name: path.read_bytes() for path in raw_dir.iterdir()} == before


def test_generate_figures_is_byte_deterministic(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    _write_raw_inputs(raw_dir)

    first = generate_figures(raw_dir, tmp_path / "figures-a")
    second = generate_figures(raw_dir, tmp_path / "figures-b")

    assert [_sha256(path) for path in first] == [_sha256(path) for path in second]


def test_missing_required_column_fails_before_writing_figures(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    _write_raw_inputs(raw_dir)
    (raw_dir / "centerpiece.csv").write_text(
        "time_s,system\n0,ours\n",
        encoding="utf-8",
    )
    output_dir = tmp_path / "figures"

    with pytest.raises(PlotInputError, match="human_p99_ms"):
        generate_figures(raw_dir, output_dir)

    assert not output_dir.exists()


@pytest.mark.parametrize(
    ("filename", "old", "new", "message"),
    [
        ("centerpiece.csv", "0,ours,10", "0,ours,nan", "finite"),
        (
            "classification.csv",
            "HUMAN_INTERACTIVE,0.9,0.8\n",
            "",
            "class values",
        ),
        (
            "classification.csv",
            "AGENT_BULK,0.7,0.6",
            "AGENT_INTERACTIVE,0.7,0.6",
            "duplicate class",
        ),
        ("classification.csv", "0.9,0.8", "1.1,0.8", "between 0 and 1"),
    ],
)
def test_invalid_aggregate_values_fail_before_writing_figures(
    tmp_path: Path,
    filename: str,
    old: str,
    new: str,
    message: str,
) -> None:
    raw_dir = tmp_path / "raw"
    _write_raw_inputs(raw_dir)
    path = raw_dir / filename
    path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
    output_dir = tmp_path / "figures"

    with pytest.raises(PlotInputError, match=message):
        generate_figures(raw_dir, output_dir)

    assert not output_dir.exists()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
