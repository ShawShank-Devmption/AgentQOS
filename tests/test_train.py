"""Training pipeline: grouped splits, schema checks, path coverage (design.md 7.14, 8 step 3)."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sklearn.tree import DecisionTreeClassifier

from common.contracts import FEATURE_CHECKPOINTS, FEATURE_ORDER, TrafficClass
from ml.train import (
    FeatureFrameError,
    PathCoverageError,
    check_path_coverage,
    cross_validation_folds,
    main,
    split_held_out,
    train,
    validate_feature_frame,
)
from tests.synthetic_corpus import synthetic_feature_frame

FRAME = synthetic_feature_frame(seed=7)


def test_held_out_split_never_splits_a_flow_and_is_seeded() -> None:
    train_part, held_out = split_held_out(FRAME, seed=3)
    assert not set(train_part["flow_id"]) & set(held_out["flow_id"])
    assert 0.1 < held_out["flow_id"].nunique() / FRAME["flow_id"].nunique() < 0.3
    again, _ = split_held_out(FRAME, seed=3)
    assert again.equals(train_part)


def test_cv_folds_keep_each_flows_checkpoints_together() -> None:
    folds = list(cross_validation_folds(FRAME, seed=3))
    assert len(folds) == 5
    for fit_rows, score_rows in folds:
        fit_flows = set(FRAME.iloc[fit_rows]["flow_id"])
        assert not fit_flows & set(FRAME.iloc[score_rows]["flow_id"])


@pytest.mark.parametrize(
    ("column", "value"),
    [("label", 0), ("checkpoint", 7), ("tls_alpn_class", 256), ("iat_ewma_us", -1)],
)
def test_validate_rejects_out_of_contract_rows(column: str, value: int) -> None:
    bad = FRAME.copy()
    bad.loc[0, column] = value
    with pytest.raises(FeatureFrameError):
        validate_feature_frame(bad)


def test_validate_rejects_missing_columns() -> None:
    with pytest.raises(FeatureFrameError, match="columns"):
        validate_feature_frame(FRAME.drop(columns=["fanout_new_flows"]))


def test_path_coverage_rejects_leaves_reached_without_timing_or_burst_split() -> None:
    features = np.zeros((4, len(FEATURE_ORDER)), dtype=np.int64)
    features[:, FEATURE_ORDER.index("updown_ratio_x100")] = [1, 1, 900, 900]
    model = DecisionTreeClassifier(random_state=0).fit(features, [1, 1, 2, 2])
    with pytest.raises(PathCoverageError, match="7.14"):
        check_path_coverage(model)


def test_train_is_deterministic_and_reports_importances_and_checkpoint_pr() -> None:
    first = train(FRAME, seed=11)
    second = train(FRAME, seed=11)
    assert first.report == second.report
    assert first.model.get_depth() <= 8
    report = first.report
    assert set(report["feature_importances"]) == set(FEATURE_ORDER)
    assert set(report["held_out"]) == {str(cp) for cp in FEATURE_CHECKPOINTS}
    per_class = report["held_out"]["16"]
    assert set(per_class) == {c.name for c in TrafficClass if c is not TrafficClass.UNKNOWN}
    assert {"precision", "recall", "support"} <= set(per_class["AGENT_BULK"])
    assert len(report["cv_macro_f1"]["folds"]) == 5


def test_cli_writes_model_report_and_held_out(tmp_path: Path) -> None:
    features = tmp_path / "features.csv"
    FRAME.to_csv(features, index=False)
    out = tmp_path / "run"
    assert main(["--features", str(features), "--seed", "5", "--out", str(out)]) == 0
    assert (out / "model.pkl").is_file()
    assert json.loads((out / "report.json").read_text())["seed"] == 5
    held_out = pd.read_csv(out / "held_out.csv")
    assert list(held_out.columns) == list(FRAME.columns)


def _session_groups() -> dict[str, str]:
    return {flow_id: f"session-{i // 6}" for i, flow_id in enumerate(FRAME["flow_id"].unique())}


def test_explicit_groups_never_cross_held_out_or_cv_and_are_seeded() -> None:
    groups = _session_groups()
    fit, held = split_held_out(FRAME, seed=3, groups=groups)
    assert not set(fit["flow_id"].map(groups)) & set(held["flow_id"].map(groups))
    assert split_held_out(FRAME, seed=3, groups=groups)[0].equals(fit)
    for fit_rows, score_rows in cross_validation_folds(FRAME, seed=3, groups=groups):
        fit_groups = set(FRAME.iloc[fit_rows]["flow_id"].map(groups))
        assert not fit_groups & set(FRAME.iloc[score_rows]["flow_id"].map(groups))


def _few_sessions_per_class(per_class: int) -> dict[str, str]:
    """Group each class's flows into `per_class` single-label sessions, like the real corpus."""
    groups = {}
    for flow_id in FRAME["flow_id"].unique():
        label, index = flow_id.rsplit("-", 1)
        groups[flow_id] = f"{label}-s{int(index) % per_class}"
    return groups


def test_held_out_takes_whole_sessions_from_every_class() -> None:
    """With few sessions per class a plain 20 % group split can leave a class untested."""
    groups = _few_sessions_per_class(4)
    for seed in range(20):
        fit, held = split_held_out(FRAME, seed=seed, groups=groups)
        held_sessions = set(held["flow_id"].map(groups))
        assert not set(fit["flow_id"].map(groups)) & held_sessions
        per_class = held.groupby("label")["flow_id"].apply(lambda f: f.map(groups).nunique())
        assert per_class.to_dict() == {1: 1, 2: 1, 3: 1}


def test_class_with_a_single_session_is_trained_on_not_held_out() -> None:
    groups = _few_sessions_per_class(4)
    for flow_id in groups:
        if flow_id.startswith("agent_bulk"):
            groups[flow_id] = "agent_bulk-only"
    fit, held = split_held_out(FRAME, seed=1, groups=groups)
    assert int(TrafficClass.AGENT_BULK) not in set(held["label"])
    assert int(TrafficClass.AGENT_BULK) in set(fit["label"])


def test_held_out_rejects_a_group_with_mixed_labels() -> None:
    groups = {flow_id: f"s{i % 7}" for i, flow_id in enumerate(FRAME["flow_id"].unique())}
    with pytest.raises(FeatureFrameError, match="single label"):
        split_held_out(FRAME, seed=1, groups=groups)


@pytest.mark.parametrize("kind", ["missing", "extra", "empty", "null"])
def test_explicit_group_mapping_must_be_complete_and_unambiguous(kind: str) -> None:
    groups = _session_groups()
    flow_id = next(iter(groups))
    if kind == "missing":
        del groups[flow_id]
    elif kind == "extra":
        groups["unobserved-flow"] = "extra"
    else:
        groups[flow_id] = "" if kind == "empty" else None
    with pytest.raises(FeatureFrameError, match="group"):
        train(FRAME, seed=11, groups=groups)


def test_too_few_independent_groups_fail_before_cross_validation() -> None:
    groups = {flow_id: "same-session" for flow_id in FRAME["flow_id"].unique()}
    with pytest.raises(FeatureFrameError, match="group"):
        list(cross_validation_folds(FRAME, seed=3, groups=groups))


@pytest.mark.parametrize(
    "kind",
    [
        "empty",
        "null_feature",
        "null_flow",
        "blank_flow",
        "null_source",
        "duplicate",
        "conflicting_label",
        "conflicting_source",
        "duplicate_column",
    ],
)
def test_feature_validation_rejects_ambiguous_or_incomplete_rows(kind: str) -> None:
    frame = FRAME.copy()
    if kind == "empty":
        frame = frame.iloc[:0]
    elif kind == "null_feature":
        frame.loc[0, "iat_ewma_us"] = np.nan
    elif kind == "null_flow":
        frame.loc[0, "flow_id"] = None
    elif kind == "blank_flow":
        frame.loc[0, "flow_id"] = " "
    elif kind == "null_source":
        frame.loc[0, "source_framework"] = None
    elif kind == "duplicate":
        frame = pd.concat([frame, frame.iloc[:1]], ignore_index=True)
    elif kind == "conflicting_label":
        frame.loc[0, "label"] = int(TrafficClass.AGENT_BULK)
    elif kind == "conflicting_source":
        frame.loc[0, "source_framework"] = "other"
    else:
        frame = pd.concat([frame, frame[["flow_id"]]], axis=1)
    with pytest.raises(FeatureFrameError):
        validate_feature_frame(frame)


def test_missing_checkpoints_report_unavailable_instead_of_predicting_empty() -> None:
    frame = FRAME[FRAME["checkpoint"] == 6]
    report = train(frame, seed=11).report
    for checkpoint in (16, 64):
        for metrics in report["held_out"][str(checkpoint)].values():
            assert metrics["support"] == 0
            assert metrics["precision"] is None
            assert metrics["recall"] is None
        confusion = report["held_out_confusion_matrices"][str(checkpoint)]
        assert confusion["sample_count"] == 0
        assert confusion["available"] is False
        assert confusion["counts"] == [[0, 0, 0], [0, 0, 0], [0, 0, 0]]


def test_source_report_exposes_browser_agents_mistaken_for_humans() -> None:
    frame = FRAME.copy()
    humans = frame[frame["label"] == int(TrafficClass.HUMAN_INTERACTIVE)]
    browser_agents = humans.copy()
    browser_agents["flow_id"] = "browser-" + browser_agents["flow_id"]
    browser_agents["label"] = int(TrafficClass.AGENT_INTERACTIVE)
    browser_agents["source_framework"] = "playwright"
    frame = pd.concat([frame, browser_agents], ignore_index=True)
    report = train(frame, seed=11).report
    source = report["held_out_by_source"]["playwright"]["16"]
    matrix = source["confusion_matrix"]
    # The report must count the actual held-out browser-agent predictions, not all agents.
    count = matrix["counts"][1][0]
    assert count > 0
    assert source["agent_as_human"]["count"] == count
    support = source["per_class"]["AGENT_INTERACTIVE"]["support"]
    assert support > 0
    assert source["agent_as_human"]["support"] == support
    assert source["agent_as_human"]["rate"] == count / support
    assert matrix["labels"] == ["HUMAN_INTERACTIVE", "AGENT_INTERACTIVE", "AGENT_BULK"]
    assert matrix["sample_count"] == support
    assert source["per_class"]["AGENT_BULK"]["recall"] is None


def test_grouped_training_records_provenance_without_adding_model_features() -> None:
    result = train(FRAME, seed=11, groups=_session_groups())
    assert result.report["grouping"]["mode"] == "explicit"
    assert result.report["grouping"]["group_count"] == 30
    assert result.report["grouping"]["flow_count"] == 180
    assert len(result.report["grouping"]["mapping_sha256"]) == 64
    assert result.model.n_features_in_ == len(FEATURE_ORDER)
    assert list(result.held_out.columns) == list(FRAME.columns)
    assert train(FRAME, seed=11).report["grouping"]["mode"] == "flow_id"


def test_cli_accepts_explicit_group_sidecar(tmp_path: Path) -> None:
    features = tmp_path / "features.csv"
    FRAME.to_csv(features, index=False)
    groups_path = tmp_path / "groups.csv"
    pd.DataFrame(_session_groups().items(), columns=["flow_id", "group_id"]).to_csv(
        groups_path, index=False
    )
    out = tmp_path / "run"
    assert (
        main(
            [
                "--features",
                str(features),
                "--groups",
                str(groups_path),
                "--seed",
                "11",
                "--out",
                str(out),
            ]
        )
        == 0
    )
    report = json.loads((out / "report.json").read_text())
    assert report["grouping"]["mode"] == "explicit"
    assert report["grouping"]["sidecar_path"] == str(groups_path)


@pytest.mark.parametrize("kind", ["duplicate", "conflict", "columns", "blank"])
def test_cli_rejects_malformed_group_sidecar(tmp_path: Path, kind: str) -> None:
    features = tmp_path / "features.csv"
    FRAME.to_csv(features, index=False)
    groups = pd.DataFrame(_session_groups().items(), columns=["flow_id", "group_id"])
    if kind in ("duplicate", "conflict"):
        extra = groups.iloc[:1].copy()
        if kind == "conflict":
            extra["group_id"] = "conflict"
        groups = pd.concat([groups, extra], ignore_index=True)
    elif kind == "columns":
        groups = groups.rename(columns={"group_id": "session_id"})
    else:
        groups.loc[0, "group_id"] = " "
    groups_path = tmp_path / "groups.csv"
    groups.to_csv(groups_path, index=False)
    out = tmp_path / "run"
    assert (
        main(
            [
                "--features",
                str(features),
                "--groups",
                str(groups_path),
                "--seed",
                "11",
                "--out",
                str(out),
            ]
        )
        == 1
    )
    assert not out.exists()
