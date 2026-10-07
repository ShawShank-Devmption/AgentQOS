"""Tree -> range-table compiler (design.md 4.5, 8 step 4)."""

import numpy as np
import pytest
from sklearn.tree import DecisionTreeClassifier

from common.compiled_tree import parse_compiled_tree
from common.contracts import (
    FEATURE_BIT_WIDTHS,
    FEATURE_ORDER,
    PUNT_REASON_LOW_CONFIDENCE,
    TREE_LEAF_ACTION,
    TREE_TABLE_NAMES,
    TrafficClass,
)
from ml.compile_tree import compile_tree, leaf_decision, left_upper_bound
from tests.synthetic_corpus import synthetic_feature_frame


def _fit(features: np.ndarray, labels: list[int], **params: object) -> DecisionTreeClassifier:
    return DecisionTreeClassifier(random_state=0, **params).fit(features, labels)


def _rows(*pkt_counts: int) -> np.ndarray:
    rows = np.zeros((len(pkt_counts), len(FEATURE_ORDER)), dtype=np.int64)
    rows[:, FEATURE_ORDER.index("pkt_count")] = pkt_counts
    return rows


@pytest.mark.parametrize("threshold", [12.5, 0.5, 16_777_216.5, 33_554_433.0, 4.0e9 + 0.5])
def test_left_upper_bound_matches_sklearn_float32_comparison(threshold: float) -> None:
    bound = left_upper_bound(threshold)
    assert np.float32(bound) <= threshold < np.float32(bound + 1)


def test_left_upper_bound_differs_from_floor_above_2_pow_24() -> None:
    assert left_upper_bound(16_777_216.5) == 16_777_217  # floor() would give 16_777_216


def test_compiled_synthetic_tree_is_schema_valid_and_deterministic() -> None:
    frame = synthetic_feature_frame(seed=1)
    model = _fit(frame[list(FEATURE_ORDER)].to_numpy(), frame["label"].tolist(), max_depth=8)
    compiled = compile_tree(model)
    tree = parse_compiled_tree(compiled)
    assert {entry.table for entry in tree.entries} <= set(TREE_TABLE_NAMES)
    assert compile_tree(model) == compiled
    assert len(compiled["model_hash"]) == 64


def test_single_leaf_tree_is_one_wildcard_entry() -> None:
    model = _fit(_rows(1, 2, 3), [1, 1, 1])
    (entry,) = compile_tree(model)["entries"]
    full = [[0, (1 << bits) - 1] for bits in FEATURE_BIT_WIDTHS]
    assert entry["match_ranges"] == [[0, 0], *full]
    assert (entry["table"], entry["action"], entry["action_params"]) == (
        "tbl_tree_l0",
        TREE_LEAF_ACTION,
        [1, 0],
    )


def test_low_purity_leaf_compiles_to_unknown_with_punt() -> None:
    model = _fit(_rows(0, 0, 0, 9, 9, 9), [1, 1, 2, 3, 3, 3], max_depth=1)
    left, right = model.tree_.children_left[0], model.tree_.children_right[0]
    assert leaf_decision(model, left) == (TrafficClass.UNKNOWN, PUNT_REASON_LOW_CONFIDENCE)
    assert leaf_decision(model, right) == (TrafficClass.AGENT_BULK, 0)
    entries = compile_tree(model)["entries"]
    assert sorted(entry["action_params"] for entry in entries) == [[0, 2], [3, 0]]


def test_tree_deeper_than_eight_levels_is_rejected() -> None:
    rng = np.random.default_rng(0)
    features = rng.integers(0, 200, size=(400, len(FEATURE_ORDER)))  # within every width
    model = _fit(features, rng.integers(1, 4, size=400).tolist())
    assert model.get_depth() > 8
    with pytest.raises(ValueError, match="depth"):
        compile_tree(model)


def test_model_with_wrong_feature_count_is_rejected() -> None:
    model = _fit(np.array([[0], [1]]), [1, 2])
    with pytest.raises(ValueError, match="features"):
        compile_tree(model)
