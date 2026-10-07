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
