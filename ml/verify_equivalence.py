"""Equivalence gate: sklearn tree vs simulated BMv2 tree tables (design.md 7.9, 8 step 5; P3.7)."""

from __future__ import annotations

import argparse
import json
import logging
import pickle
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.tree import DecisionTreeClassifier

from common.compiled_tree import CompiledTree, classify, load_compiled_tree
from common.contracts import FEATURE_ORDER
from ml.compile_tree import leaf_decision, model_hash

LOGGER = logging.getLogger(__name__)

_LEAF = -1


@dataclass(frozen=True)
class EquivalenceReport:
    """Rows checked and the positions where the tables disagreed with sklearn."""

    total: int
    mismatches: tuple[int, ...]


def verify(
    model: DecisionTreeClassifier, compiled: CompiledTree, features: np.ndarray
) -> EquivalenceReport:
    """Compare sklearn's leaf decision with the simulated tables for every row.

    A row agrees when (class, punt_reason) match the compile rule applied to sklearn's leaf, and
    the tables reach that leaf from the same parent node at the same level.

    Raises:
        ValueError: If the compiled tree was built from a different model.
    """
    if compiled.model_hash != model_hash(model):
        raise ValueError("compiled model_hash does not match the model under test")
    rows = np.asarray(features, dtype=np.int64)
    parents, depths = _parents_and_depths(model)
    mismatches = []
    for position, (row, leaf) in enumerate(zip(rows, model.apply(rows), strict=True)):
        decision = classify(compiled, tuple(int(value) for value in row))
        expected_path = (max(depths[leaf] - 1, 0), max(parents[leaf], 0))
        if (decision.traffic_class, decision.punt_reason) != leaf_decision(model, leaf) or (
            decision.leaf_level,
            decision.last_node,
        ) != expected_path:
            mismatches.append(position)
    return EquivalenceReport(len(rows), tuple(mismatches))


def main(argv: Sequence[str] | None = None) -> int:
    """Run the gate on held-out rows; exit non-zero unless agreement is 100%.

    Returns:
        Zero when every row agrees, otherwise one.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--compiled", type=Path, required=True)
    parser.add_argument("--held-out", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=None)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        model = pickle.loads(args.model.read_bytes())  # our own train.py artifact
        compiled = load_compiled_tree(args.compiled)
        features = pd.read_csv(args.held_out)[list(FEATURE_ORDER)].to_numpy()
        report = verify(model, compiled, features)
    except (OSError, KeyError, ValueError, pickle.UnpicklingError) as exc:
        LOGGER.error("equivalence gate could not run: %s", exc)
        return 1
    if args.report is not None:
        summary = {
            "model_hash": compiled.model_hash,
            "total": report.total,
            "mismatches": len(report.mismatches),
            "first_mismatch_rows": list(report.mismatches[:20]),
        }
        args.report.write_text(json.dumps(summary, indent=2) + "\n")
    if report.mismatches:
        LOGGER.error(
            "equivalence FAILED: %d of %d rows differ", len(report.mismatches), report.total
        )
        return 1
    LOGGER.info("equivalence passed: %d of %d rows agree", report.total, report.total)
    return 0


def _parents_and_depths(model: DecisionTreeClassifier) -> tuple[list[int], list[int]]:
    tree = model.tree_
    parents = [-1] * tree.node_count
    depths = [0] * tree.node_count
    for node in range(tree.node_count):
        for child in (tree.children_left[node], tree.children_right[node]):
            if child != _LEAF:
                parents[child] = node
                depths[child] = depths[node] + 1
    return parents, depths


if __name__ == "__main__":
    raise SystemExit(main())
