"""BMv2 range-table simulation of the compiled tree (design.md 5.3, 7.9)."""

import pytest

from common.compiled_tree import CompiledTreeError, classify, parse_compiled_tree
from common.contracts import (
    FEATURE_BIT_WIDTHS,
    FEATURE_ORDER,
    LOW_CONFIDENCE_PURITY,
    PUNT_REASON_LOW_CONFIDENCE,
    TREE_MAX_DEPTH,
    TREE_TABLE_NAMES,
    TrafficClass,
)

FULL = [[0, (1 << bits) - 1] for bits in FEATURE_BIT_WIDTHS]


def _entry(level: int, node: int, action: str, params: list[int], **feature_range: list) -> dict:
    ranges = [list(r) for r in FULL]
    for name, bounds in feature_range.items():
        ranges[FEATURE_ORDER.index(name)] = bounds
    return {
        "table": f"tbl_tree_l{level}",
        "match_ranges": [[node, node], *ranges],
        "action": action,
        "action_params": params,
        "priority": 1,
    }


# node 0 splits pkt_count at 10: left -> node 1, right -> leaf AGENT_BULK.
# node 1 splits iat_ewma_us at 500: left -> leaf AGENT_INTERACTIVE, right -> low-confidence leaf.
TREE = parse_compiled_tree(
    {
        "model_hash": "hand",
        "feature_order": list(FEATURE_ORDER),
        "entries": [
            _entry(0, 0, "tree_next", [1], pkt_count=[0, 10]),
            _entry(0, 0, "tree_leaf", [3, 0], pkt_count=[11, (1 << 32) - 1]),
            _entry(1, 1, "tree_leaf", [2, 0], iat_ewma_us=[0, 500]),
            _entry(1, 1, "tree_leaf", [0, 2], iat_ewma_us=[501, (1 << 32) - 1]),
        ],
    }
)


def _vector(**values: int) -> tuple[int, ...]:
    return tuple(values.get(name, 0) for name in FEATURE_ORDER)


def test_contract_constants_for_tree() -> None:
    assert TREE_MAX_DEPTH == len(TREE_TABLE_NAMES) == 8
    assert LOW_CONFIDENCE_PURITY == 0.85


def test_two_level_path_to_leaf() -> None:
    decision = classify(TREE, _vector(pkt_count=6, iat_ewma_us=500))
    assert (decision.traffic_class, decision.punt_reason, decision.leaf_level) == (
        TrafficClass.AGENT_INTERACTIVE,
        0,
        1,
    )


def test_leaf_reached_at_level_zero() -> None:
    decision = classify(TREE, _vector(pkt_count=11))
    assert (decision.traffic_class, decision.leaf_level) == (TrafficClass.AGENT_BULK, 0)


def test_low_confidence_leaf_punts() -> None:
    decision = classify(TREE, _vector(pkt_count=10, iat_ewma_us=501))
    assert (decision.traffic_class, decision.punt_reason) == (
        TrafficClass.UNKNOWN,
        PUNT_REASON_LOW_CONFIDENCE,
    )


def test_table_miss_is_fail_open_unknown() -> None:
    sparse = parse_compiled_tree(
        {
            "model_hash": "sparse",
            "feature_order": list(FEATURE_ORDER),
            "entries": [_entry(0, 0, "tree_next", [1], pkt_count=[0, 10])],
        }
    )
    decision = classify(sparse, _vector(pkt_count=6))
    assert (decision.traffic_class, decision.punt_reason) == (TrafficClass.UNKNOWN, 0)


def test_overlapping_entries_are_a_compiler_bug() -> None:
    overlapping = parse_compiled_tree(
        {
            "model_hash": "overlap",
            "feature_order": list(FEATURE_ORDER),
            "entries": [
                _entry(0, 0, "tree_leaf", [1, 0], pkt_count=[0, 10]),
                _entry(0, 0, "tree_leaf", [2, 0], pkt_count=[5, 20]),
            ],
        }
    )
    with pytest.raises(CompiledTreeError, match="ambiguous"):
        classify(overlapping, _vector(pkt_count=7))


def test_vector_length_must_match_feature_order() -> None:
    with pytest.raises(ValueError):
        classify(TREE, (1, 2, 3))
