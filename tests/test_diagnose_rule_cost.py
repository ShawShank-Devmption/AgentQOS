"""Rule-cost diagnostic: both trees share one session-held-out split; timing_only obeys 7.14."""

import json
from pathlib import Path

import pandas as pd
import pytest

from common.contracts import TIMING_BURST_FEATURES
from ml.diagnose_rule_cost import STATIC_FEATURES, evaluate, main, timing_only
from tests.synthetic_corpus import synthetic_feature_frame

FRAME = synthetic_feature_frame(seed=7)


def _sessions(per_class: int = 5) -> dict[str, str]:
    groups = {}
    for flow_id in FRAME["flow_id"].unique():
        label, index = flow_id.rsplit("-", 1)
        groups[flow_id] = f"{label}-s{int(index) % per_class}"
    return groups


def test_timing_only_view_hides_every_static_feature() -> None:
    view = timing_only(FRAME)
    assert (view[list(STATIC_FEATURES)] == 0).all().all()
    assert view[list(TIMING_BURST_FEATURES)].equals(FRAME[list(TIMING_BURST_FEATURES)])
    assert FRAME[list(STATIC_FEATURES)].ne(0).any().any()  # the original is untouched


def test_both_models_scored_on_whole_held_out_sessions_of_every_class() -> None:
    groups = _sessions()
    result = evaluate(FRAME, seed=3, groups=groups)
    held = result["held_out_groups"]
    assert {group.rsplit("-s", 1)[0] for group in held} == {
        "human_interactive",
        "agent_interactive",
        "agent_bulk",
    }
    timing, free = result["models"]["timing_only"], result["models"]["unconstrained"]
    assert timing["leaves_failing_7_14"] == 0
    assert all(timing["feature_importances"][name] == 0 for name in STATIC_FEATURES)
    for model in (timing, free):
        assert set(model["held_out"]) == {"6", "16", "64"}
        assert 0.0 <= model["cv_macro_f1"]["mean"] <= 1.0
    assert evaluate(FRAME, seed=3, groups=groups) == result


def test_cli_writes_a_new_report_and_never_overwrites(tmp_path: Path) -> None:
    features = tmp_path / "features.csv"
    FRAME.to_csv(features, index=False)
    sidecar = tmp_path / "groups.csv"
    pd.DataFrame(list(_sessions().items()), columns=["flow_id", "group_id"]).to_csv(
        sidecar, index=False
    )
    out = tmp_path / "rule_cost.json"
    args = ["--features", str(features), "--groups", str(sidecar), "--seed", "1", "--out", str(out)]
    assert main(args) == 0
    assert set(json.loads(out.read_text())["models"]) == {"unconstrained", "timing_only"}
    assert main(args) == 1


def test_mixed_label_session_is_rejected() -> None:
    groups = {flow_id: f"s{i % 7}" for i, flow_id in enumerate(FRAME["flow_id"].unique())}
    with pytest.raises(ValueError, match="single label"):
        evaluate(FRAME, seed=1, groups=groups)
