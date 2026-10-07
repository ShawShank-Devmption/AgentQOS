"""Compile a fitted sklearn tree into tbl_tree_l* range entries (design.md 4.5, 8 step 4; P3.6)."""

from __future__ import annotations

import hashlib
import json
import math

import numpy as np
from sklearn.tree import DecisionTreeClassifier

from common.contracts import (
    FEATURE_BIT_WIDTHS,
    FEATURE_ORDER,
    LOW_CONFIDENCE_PURITY,
    PUNT_REASON_LOW_CONFIDENCE,
    TREE_LEAF_ACTION,
    TREE_MAX_DEPTH,
    TREE_NEXT_ACTION,
    TREE_NODE_BITS,
    TREE_TABLE_NAMES,
    TrafficClass,
)

_LEAF = -1  # sklearn marks leaves with children_left == -1
_FULL_RANGES = tuple((0, (1 << bits) - 1) for bits in FEATURE_BIT_WIDTHS)


def left_upper_bound(threshold: float) -> int:
    """Return the largest integer k with `float32(k) <= threshold`.

    sklearn casts inputs to float32 before comparing with the float64 threshold, so above 2**24
    `floor(threshold)` is not the switch-side boundary that reproduces its decisions (7.9).
    """
    bound = math.floor(threshold)
    while np.float32(bound + 1) <= threshold:
        bound += 1
    while bound >= 0 and np.float32(bound) > threshold:
        bound -= 1
    return bound


def leaf_decision(model: DecisionTreeClassifier, node: int) -> tuple[TrafficClass, int]:
    """Return the (class, punt_reason) a leaf compiles to.

    Leaves whose class-weighted purity is below LOW_CONFIDENCE_PURITY become UNKNOWN with a
    reason-2 punt instead of a hard label (7.8, 8 step 4).
    """
    value = model.tree_.value[node][0]
    if value.max() / value.sum() < LOW_CONFIDENCE_PURITY:
        return TrafficClass.UNKNOWN, PUNT_REASON_LOW_CONFIDENCE
    return TrafficClass(int(model.classes_[int(np.argmax(value))])), 0


def compile_tree(model: DecisionTreeClassifier) -> dict:
    """Compile a fitted tree into the section 4.5 JSON document.

    Each internal node at depth d emits two entries in `tbl_tree_l{d}` with disjoint ranges on
    its split feature and wildcards elsewhere; a leaf child is reached through `tree_leaf`.

    Raises:
        ValueError: If the model does not use the frozen features or exceeds the table depth.
    """
    if model.n_features_in_ != len(FEATURE_ORDER):
        raise ValueError(
            f"model has {model.n_features_in_} features, expected {len(FEATURE_ORDER)}"
        )
    if model.get_depth() > TREE_MAX_DEPTH:
        raise ValueError(f"tree depth {model.get_depth()} exceeds {TREE_MAX_DEPTH} table levels")
    tree = model.tree_
    if tree.children_left[0] == _LEAF:
        entries = [_entry(0, 0, _leaf_action(model, 0), _FULL_RANGES)]
    else:
        entries = []
        pending = [(0, 0)]
        while pending:
            node, depth = pending.pop()
            for child, bounds in _child_bounds(model, node):
                ranges = list(_FULL_RANGES)
                ranges[tree.feature[node]] = bounds
                if tree.children_left[child] == _LEAF:
                    action = _leaf_action(model, child)
                else:
                    action = (TREE_NEXT_ACTION, [_node_id(child)])
                    pending.append((child, depth + 1))
                entries.append(_entry(depth, node, action, ranges))
    entries.sort(key=lambda e: (e["table"], e["match_ranges"]))
    return {
        "model_hash": model_hash(model),
        "feature_order": list(FEATURE_ORDER),
        "entries": entries,
    }


def model_hash(model: DecisionTreeClassifier) -> str:
    """Deterministic sha256 of the tree structure (pickle bytes are not stable)."""
    tree = model.tree_
    canonical = {
        "children_left": tree.children_left.tolist(),
        "children_right": tree.children_right.tolist(),
        "feature": tree.feature.tolist(),
        "threshold": tree.threshold.tolist(),
        "value": tree.value.tolist(),
        "classes": [int(c) for c in model.classes_],
    }
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()


def _child_bounds(
    model: DecisionTreeClassifier, node: int
) -> tuple[tuple[int, tuple[int, int]], ...]:
    tree = model.tree_
    feature = int(tree.feature[node])
    maximum = _FULL_RANGES[feature][1]
    split = left_upper_bound(float(tree.threshold[node]))
    if not 0 <= split < maximum:
        raise ValueError(f"node {node} threshold leaves an empty side on {FEATURE_ORDER[feature]}")
    return (
        (int(tree.children_left[node]), (0, split)),
        (int(tree.children_right[node]), (split + 1, maximum)),
    )


def _leaf_action(model: DecisionTreeClassifier, node: int) -> tuple[str, list[int]]:
    traffic_class, punt_reason = leaf_decision(model, node)
    return TREE_LEAF_ACTION, [int(traffic_class), punt_reason]


def _node_id(node: int) -> int:
    if node >= 1 << TREE_NODE_BITS:
        raise ValueError(f"node id {node} does not fit tree_node")
    return node


def _entry(level: int, node: int, action: tuple[str, list[int]], ranges: list | tuple) -> dict:
    name, params = action
    return {
        "table": TREE_TABLE_NAMES[level],
        "match_ranges": [[node, node], *([low, high] for low, high in ranges)],
        "action": name,
        "action_params": params,
        "priority": 1,
    }


GENERATED_BEGIN = "// BEGIN GENERATED: ml/compile_tree.py (do not edit)"
GENERATED_END = "// END GENERATED"


def emit_p4_declarations() -> str:
    """Return the tbl_tree_l* declarations for classifier.p4, derived only from contracts.

    DESIGN GAP G19: key paths `meta.tree_node` / `meta.features.<name>` and the actions
    `tree_next` / `tree_leaf` must match Dev A's metadata struct and action definitions.
    """
    keys = "\n".join(
        ["            meta.tree_node : exact;"]
        + [f"            meta.features.{name} : range;" for name in FEATURE_ORDER]
    )
    tables = [
        f"""    table {name} {{
        key = {{
{keys}
        }}
        actions = {{ {TREE_NEXT_ACTION}; {TREE_LEAF_ACTION}; NoAction; }}
        default_action = NoAction();
        size = {2 ** (level + 1)};
    }}"""
        for level, name in enumerate(TREE_TABLE_NAMES)
    ]
    return "\n\n".join(tables)


def splice_generated(p4_source: str, declarations: str) -> str:
    """Replace the text between the generated-section markers of classifier.p4.

    Raises:
        ValueError: If the markers are missing or out of order.
    """
    begin = p4_source.find(GENERATED_BEGIN)
    end = p4_source.find(GENERATED_END)
    if begin < 0 or end < begin:
        raise ValueError("classifier.p4 lacks the generated-section markers")
    head = p4_source[: begin + len(GENERATED_BEGIN)]
    indent = p4_source[p4_source.rfind("\n", 0, end) + 1 : end]
    return f"{head}\n{declarations}\n{indent}{p4_source[end:]}"
