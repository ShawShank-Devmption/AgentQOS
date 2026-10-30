"""Diagnostic regressions on hand-built features, never the synthetic generator."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from common.contracts import FEATURE_ORDER
from ml import diagnose_browser as diagnosis


def _config() -> dict:
    return {
        "kind": "local_browser_diagnosis_v1",
        "seeds": [1, 2, 3],
        "cv_folds": 2,
        "held_out_fraction": 0.25,
        "tree_max_depth": 3,
        "random_forest_trees": 0,
        "feature_sets": {"full": list(FEATURE_ORDER), "timing": ["iat_ewma_us"]},
        "profiles": ["human", "browser", "absent"],
        "browser_profiles": ["human", "browser"],
    }


def _frame() -> tuple[pd.DataFrame, dict[str, str]]:
    rows, groups = [], {}
    # Opposite labels are indistinguishable at every observed feature vector.
    for session in range(12):
        for label, profile in [(1, "human"), (2, "browser")]:
            for flow in range(2):
                flow_id = f"{session}:{label}:{flow}"
                groups[flow_id] = f"session-{session}"
                row = dict.fromkeys(FEATURE_ORDER, 0)
                row.update(pkt_count=6, iat_ewma_us=session)
                rows.append(
                    dict(
                        flow_id=flow_id, checkpoint=6, **row, label=label, source_framework=profile
                    )
                )
    return pd.DataFrame(rows), groups


def test_opposite_label_aliasing_is_empirical_not_population_bound() -> None:
    frame, _ = _frame()
    report = diagnosis.feature_overlap(frame, FEATURE_ORDER)
    assert report["6"]["conflicting_vectors"] == 12
    assert report["6"]["conflicting_rows"] == 48
    assert report["6"]["observed_min_errors"] == 24
    assert report["6"]["observed_deterministic_accuracy_ceiling"] == 0.5
    assert report["16"]["observed_deterministic_accuracy_ceiling"] is None
    assert report["16"]["rows"] == 0


def test_reproducible_diagnosis_has_disjoint_sessions_and_handles_absence() -> None:
    frame, groups = _frame()
    config = diagnosis.parse_config(_config())
    report = diagnosis.diagnose(frame, groups, config)
    assert report == diagnosis.diagnose(frame, groups, config)
    assert report["features"] == list(FEATURE_ORDER)
    for run in report["runs"]:
        assert run["held_out"]["split"]["shared_groups"] == 0
        assert run["held_out"]["metrics"]["checkpoints"]["16"]["rows"] == 0
        assert run["held_out"]["metrics"]["profiles"]["absent"]["rows"] == 0
        assert run["held_out"]["metrics"]["all"]["accuracy"] <= 0.5
        for result in run["cross_validation"]:
            assert result["metrics"]["all"]["accuracy"] <= 0.5
            assert all(fold["shared_flows"] == 0 for fold in result["splits"])
            if result["grouping"] == "session":
                assert all(fold["shared_groups"] == 0 for fold in result["splits"])


def test_identifiers_and_source_metadata_cannot_be_used_as_features() -> None:
    for column in ["label", "flow_id", "group_id", "source_framework", "checkpoint"]:
        raw = _config()
        raw["feature_sets"]["leak"] = [column]
        with pytest.raises(ValueError, match="feature"):
            diagnosis.parse_config(raw)


@pytest.mark.parametrize(
    "changes",
    [
        {"seeds": [1, 1, 2]},
        {"seeds": [True, 2, 3]},
        {"seeds": [1]},
        {"tree_max_depth": 9},
        {"cv_folds": 1},
        {"held_out_fraction": 0},
        {"random_forest_trees": -1},
        {"unknown": 1},
        {"profiles": "human"},
        {"feature_sets": {"full": ["iat_ewma_us"]}},
    ],
)
def test_invalid_config_fails_fast(changes: dict) -> None:
    raw = _config()
    raw.update(changes)
    with pytest.raises(ValueError):
        diagnosis.parse_config(raw)


def test_mapping_must_be_explicit_and_complete() -> None:
    frame, groups = _frame()
    groups.pop(next(iter(groups)))
    with pytest.raises(ValueError, match="mapping"):
        diagnosis.diagnose(frame, groups, diagnosis.parse_config(_config()))


def test_empty_corpus_and_too_few_sessions_fail() -> None:
    frame, groups = _frame()
    with pytest.raises(ValueError, match="empty"):
        diagnosis.diagnose(frame.iloc[:0], {}, diagnosis.parse_config(_config()))
    with pytest.raises(ValueError, match="groups"):
        diagnosis.diagnose(frame, dict.fromkeys(groups, "one"), diagnosis.parse_config(_config()))


def test_cli_preserves_existing_evidence_and_rejects_duplicate_mapping(tmp_path: Path) -> None:
    frame, groups = _frame()
    features, mapping, config, out = [
        tmp_path / name for name in ["f.csv", "g.csv", "c.json", "out"]
    ]
    frame.to_csv(features, index=False)
    pd.DataFrame(groups.items(), columns=["flow_id", "group_id"]).to_csv(mapping, index=False)
    config.write_text(json.dumps(_config()))
    args = [
        "--features",
        str(features),
        "--groups",
        str(mapping),
        "--config",
        str(config),
        "--out",
        str(out),
    ]
    assert diagnosis.main(args) == 0
    assert sorted(path.name for path in out.iterdir()) == ["report.json"]
    saved = (out / "report.json").read_bytes()
    assert diagnosis.main(args) == 1
    assert (out / "report.json").read_bytes() == saved
    duplicate = pd.read_csv(mapping)
    pd.concat([duplicate, duplicate.iloc[:1]]).to_csv(mapping, index=False)
    args[-1] = str(tmp_path / "other")
    assert diagnosis.main(args) == 1
    assert not (tmp_path / "other").exists()


def test_feature_ablation_uses_only_the_declared_features() -> None:
    frame, groups = _frame()
    frame["iat_ewma_us"] = frame["label"] * 100
    raw = _config()
    raw["feature_sets"]["size_only"] = ["mean_pkt_size_up"]
    raw["random_forest_trees"] = 5
    report = diagnosis.diagnose(frame, groups, diagnosis.parse_config(raw))
    for run in report["runs"]:
        for result in run["cross_validation"]:
            expected = 0.5 if result["feature_set"] == "size_only" else 1.0
            assert result["metrics"]["all"]["accuracy"] == expected
    assert report == diagnosis.diagnose(frame, groups, diagnosis.parse_config(raw))
