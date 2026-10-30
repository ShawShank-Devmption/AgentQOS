"""Hand-checked measured evaluation fixtures, never synthetic performance claims."""

import math
from pathlib import Path

import pytest

from common.contracts import FEATURE_CHECKPOINTS, TrafficClass
from eval import metrics


def observations() -> dict[str, object]:
    """Return four measured packet samples with one incomplete tool call."""
    return {
        "measurement_start_s": 0.0,
        "measurement_end_s": 2.0,
        "packets": [
            {
                "packet_id": str(index),
                "traffic_class": int(TrafficClass.HUMAN_INTERACTIVE),
                "sent_s": 0.0,
                "received_s": delay,
                "size_bytes": 1000,
            }
            for index, delay in enumerate([0.01, 0.02, 0.03, 0.04])
        ],
        "tool_calls": [
            {"call_id": "a", "start_s": 0.0, "end_s": 0.5},
            {"call_id": "b", "start_s": 0.1, "end_s": None},
        ],
        "classifications": [
            {
                "flow_id": "a",
                "checkpoint": checkpoint,
                "truth": int(TrafficClass.HUMAN_INTERACTIVE),
                "predicted": int(TrafficClass.HUMAN_INTERACTIVE),
                "label_source": "orchestration",
            }
            for checkpoint in FEATURE_CHECKPOINTS
        ],
    }


def test_hand_checked_percentiles_throughput_completion_and_checkpoints() -> None:
    summary = metrics.summarize_observations(observations())
    human = summary["classes"]["HUMAN_INTERACTIVE"]
    assert human["latency_ms"] == pytest.approx({"p50": 25, "p95": 38.5, "p99": 39.7})
    assert human["throughput_mbps"] == pytest.approx(0.016)
    assert summary["classes"]["AGENT_BULK"]["latency_ms"] is None
    assert summary["tool_calls"]["completed"] == 1
    assert summary["tool_calls"]["incomplete"] == 1
    assert summary["tool_calls"]["completion_fraction"] == 0.5
    assert summary["tool_calls"]["completion_ms"]["p99"] == 500
    for checkpoint in FEATURE_CHECKPOINTS:
        report = summary["classification"][str(checkpoint)]
        assert report["HUMAN_INTERACTIVE"]["recall"] == 1.0
        assert report["AGENT_BULK"]["precision"] is None


def test_unknown_predictions_count_as_misses_and_unlabeled_background_is_absent() -> None:
    raw = observations()
    raw["classifications"][0]["predicted"] = int(TrafficClass.UNKNOWN)
    summary = metrics.summarize_observations(raw)
    assert summary["classification"]["6"]["HUMAN_INTERACTIVE"]["recall"] == 0
    assert summary["classification"]["6"]["UNKNOWN"]["predicted"] == 1


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.1, True, "0.1"])
def test_invalid_packet_measurements_fail(value: object) -> None:
    raw = observations()
    raw["packets"][0]["received_s"] = value
    with pytest.raises(metrics.MeasurementError):
        metrics.summarize_observations(raw)


@pytest.mark.parametrize("change", ["missing", "empty", "duplicate", "leak", "checkpoint"])
def test_missing_duplicate_or_leaking_data_is_rejected(change: str) -> None:
    raw = observations()
    if change == "missing":
        del raw["packets"][0]["sent_s"]
    elif change == "empty":
        raw["packets"] = []
    elif change == "duplicate":
        raw["packets"].append(raw["packets"][0])
    elif change == "leak":
        raw["classifications"][0]["label_source"] = "traffic_features"
    else:
        raw["classifications"][0]["checkpoint"] = 8
    with pytest.raises(metrics.MeasurementError):
        metrics.summarize_observations(raw)


def test_lost_packet_and_incomplete_calls_are_reported_without_zero_latency() -> None:
    raw = observations()
    for packet in raw["packets"]:
        packet["received_s"] = None
    raw["tool_calls"] = []
    summary = metrics.summarize_observations(raw)
    human = summary["classes"]["HUMAN_INTERACTIVE"]
    assert human["lost"] == 4
    assert human["latency_ms"] is None
    assert human["throughput_mbps"] == 0
    assert summary["tool_calls"]["completion_fraction"] is None


def test_seed_level_t_confidence_interval_and_validation() -> None:
    estimate = metrics.seed_confidence_interval({seed: seed for seed in range(1, 6)})
    assert estimate.mean == 3
    assert estimate.lower == pytest.approx(3 - 2.776445105 * math.sqrt(0.5))
    assert estimate.upper == pytest.approx(3 + 2.776445105 * math.sqrt(0.5))
    assert estimate.samples == 5
    with pytest.raises(metrics.MeasurementError):
        metrics.seed_confidence_interval({1: 1, 2: 2})
    with pytest.raises(metrics.MeasurementError):
        metrics.seed_confidence_interval({seed: float("nan") for seed in range(5)})


def test_anchor_checks_use_best_of_every_baseline_and_strict_overhead() -> None:
    baselines = {name: 100.0 for name in ("fifo", "diffserv", "fairq", "app_limiter")}
    result = metrics.assert_anchor_targets(70.0, baselines, 104.9, 100.0)
    assert result.p99_reduction == pytest.approx(0.30)
    with pytest.raises(metrics.AnchorError, match="overhead"):
        metrics.assert_anchor_targets(70.0, baselines, 105.0, 100.0)
    baselines["fairq"] = 90.0
    with pytest.raises(metrics.AnchorError, match="p99"):
        metrics.assert_anchor_targets(70.0, baselines, 100.0, 100.0)
    del baselines["fairq"]
    with pytest.raises(metrics.MeasurementError, match="baseline"):
        metrics.assert_anchor_targets(1.0, baselines, 100.0, 100.0)


def test_metrics_cli_writes_summary_from_raw_only(tmp_path: Path) -> None:
    import json

    source = tmp_path / "observations.json"
    source.write_text(json.dumps(observations()), encoding="utf-8")
    output = tmp_path / "summary.json"
    assert metrics.main([str(source), "--output", str(output)]) == 0
    assert json.loads(output.read_text())["classes"]["HUMAN_INTERACTIVE"]["received"] == 4


def test_aggregation_requires_independent_seeds_and_preserves_missing_metrics(
    tmp_path: Path,
) -> None:
    import hashlib
    import json

    from eval.run_experiment import ExperimentConfig, config_hash, config_values

    config = ExperimentConfig(
        "grid", "ours", "choke_v1", 20, (30,), ("low",), 2, (1, 2, 3, 4, 5), Path("results/grid")
    )
    runs = []
    for seed in config.seeds:
        run = tmp_path / str(seed)
        run.mkdir()
        raw = observations()
        for packet in raw["packets"]:
            packet["received_s"] *= seed
        source = run / "observations.json"
        source.write_text(json.dumps(raw), encoding="utf-8")
        (run / "started.json").write_text(
            json.dumps(
                {
                    "config_hash": config_hash(config),
                    "config": config_values(config),
                    "seed": seed,
                    "agent_share_pct": 30,
                    "burst_intensity": "low",
                }
            ),
            encoding="utf-8",
        )
        (run / "completed.json").write_text(
            json.dumps(
                {
                    "status": "success",
                    "returncode": 0,
                    "observations_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                }
            ),
            encoding="utf-8",
        )
        runs.append(run)
    aggregates = metrics.aggregate_runs(runs)
    assert len(aggregates) == 1
    human_p99 = aggregates[0]["metrics"]["classes.HUMAN_INTERACTIVE.latency_ms.p99"]
    assert human_p99["estimate"]["mean"] == pytest.approx(39.7 * 3)
    assert human_p99["estimate"]["samples"] == 5
    absent = aggregates[0]["metrics"]["classes.AGENT_BULK.latency_ms.p99"]
    assert absent["estimate"] is None
    assert absent["unavailable_seeds"] == [1, 2, 3, 4, 5]
    with pytest.raises(metrics.MeasurementError, match="duplicate"):
        metrics.aggregate_runs(runs + [runs[0]])
    with pytest.raises(metrics.MeasurementError, match="seed"):
        metrics.aggregate_runs(runs[:-1])
    assert (
        metrics.main(
            ["--runs", *(str(run) for run in runs), "--output", str(tmp_path / "aggregate.json")]
        )
        == 0
    )
    (runs[0] / "observations.json").write_text("{}", encoding="utf-8")
    with pytest.raises(metrics.MeasurementError, match="hash"):
        metrics.aggregate_runs(runs)


def test_json_duplicate_fields_are_rejected_at_input_boundary(tmp_path: Path) -> None:
    source = tmp_path / "observations.json"
    import json

    serialized = json.dumps(observations())
    source.write_text(
        serialized.replace(
            '"measurement_end_s": 2.0', '"measurement_end_s": 2.0, "measurement_end_s": 3.0'
        )
    )
    with pytest.raises(metrics.MeasurementError, match="duplicate"):
        metrics.load_observations(source)


@pytest.mark.parametrize(
    "change",
    [
        "duplicate_classification",
        "conflicting_truth",
        "unknown_truth",
        "duplicate_tool",
        "backwards_tool",
        "extra_field",
        "bool_class",
    ],
)
def test_strict_label_and_completion_boundaries(change: str) -> None:
    raw = observations()
    if change == "duplicate_classification":
        raw["classifications"].append(raw["classifications"][0])
    elif change == "conflicting_truth":
        raw["classifications"][1]["truth"] = int(TrafficClass.AGENT_BULK)
    elif change == "unknown_truth":
        raw["classifications"][0]["truth"] = int(TrafficClass.UNKNOWN)
    elif change == "duplicate_tool":
        raw["tool_calls"].append(raw["tool_calls"][0])
    elif change == "backwards_tool":
        raw["tool_calls"][1]["end_s"] = 0.01
    elif change == "extra_field":
        raw["classifications"][0]["features"] = [1, 2, 3]
    else:
        raw["packets"][0]["traffic_class"] = True
    with pytest.raises(metrics.MeasurementError):
        metrics.summarize_observations(raw)
