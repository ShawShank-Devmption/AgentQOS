"""Train the depth-8 classifier tree on the integerized feature table (design.md 8 step 3; P3.6)."""

from __future__ import annotations

import argparse
import json
import logging
import pickle
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import f1_score, precision_recall_fscore_support
from sklearn.model_selection import GroupShuffleSplit, StratifiedGroupKFold
from sklearn.tree import DecisionTreeClassifier

from common.contracts import (
    FEATURE_BIT_WIDTHS,
    FEATURE_CHECKPOINTS,
    FEATURE_ORDER,
    TIMING_BURST_FEATURES,
    TRAINING_LABELS,
    TREE_MAX_DEPTH,
)
from ml.compile_tree import model_hash

LOGGER = logging.getLogger(__name__)

FEATURE_FRAME_COLUMNS = ("flow_id", "checkpoint", *FEATURE_ORDER, "label", "source_framework")
HELD_OUT_FRACTION = 0.2
CV_FOLDS = 5
_LEAF = -1
_TIMING_BURST_INDICES = frozenset(FEATURE_ORDER.index(name) for name in TIMING_BURST_FEATURES)


class FeatureFrameError(ValueError):
    """Raised when the feature table violates the extraction contract."""


class PathCoverageError(ValueError):
    """Raised when a tree path classifies without any timing/burst feature (7.14)."""


@dataclass(frozen=True)
class TrainResult:
    """A fitted tree, its untouched held-out rows, and the paper-facing report."""

    model: DecisionTreeClassifier
    held_out: pd.DataFrame
    report: dict


def validate_feature_frame(frame: pd.DataFrame) -> None:
    """Check columns, labels, checkpoints and feature widths before training.

    Raises:
        FeatureFrameError: On any violation.
    """
    missing = set(FEATURE_FRAME_COLUMNS) - set(frame.columns)
    if missing:
        raise FeatureFrameError(f"feature table lacks columns {sorted(missing)}")
    if not frame["label"].isin([int(label) for label in TRAINING_LABELS]).all():
        raise FeatureFrameError("labels must be 1, 2 or 3 (UNKNOWN never trains, 4.4)")
    if not frame["checkpoint"].isin(FEATURE_CHECKPOINTS).all():
        raise FeatureFrameError(f"checkpoints must be in {FEATURE_CHECKPOINTS}")
    for name, bits in zip(FEATURE_ORDER, FEATURE_BIT_WIDTHS, strict=True):
        column = frame[name]
        if not pd.api.types.is_integer_dtype(column):
            raise FeatureFrameError(f"{name} must be integer fixed-point values")
        if column.min() < 0 or column.max() >= 1 << bits:
            raise FeatureFrameError(f"{name} has values outside its {bits}-bit width")


def split_held_out(frame: pd.DataFrame, seed: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split off HELD_OUT_FRACTION of flows; a flow's checkpoint rows never straddle the split."""
    splitter = GroupShuffleSplit(n_splits=1, test_size=HELD_OUT_FRACTION, random_state=seed)
    train_rows, held_rows = next(splitter.split(frame, groups=frame["flow_id"]))
    return frame.iloc[train_rows], frame.iloc[held_rows]


def cross_validation_folds(
    frame: pd.DataFrame, seed: int
) -> Iterator[tuple[np.ndarray, np.ndarray]]:
    """Yield (fit, score) row positions for stratified CV grouped by flow."""
    folds = StratifiedGroupKFold(n_splits=CV_FOLDS, shuffle=True, random_state=seed)
    yield from folds.split(frame, frame["label"], groups=frame["flow_id"])


def check_path_coverage(model: DecisionTreeClassifier) -> None:
    """Assert every root-to-leaf path splits on a timing/burst feature (7.14).

    A leaf reachable only through size/ratio/TLS splits would classify one-way-visible flows on
    a degraded ratio alone.

    Raises:
        PathCoverageError: Naming the uncovered leaves.
    """
    tree = model.tree_
    uncovered = []
    pending = [(0, False)]
    while pending:
        node, covered = pending.pop()
        if tree.children_left[node] == _LEAF:
            if not covered:
                uncovered.append(int(node))
            continue
        covered = covered or int(tree.feature[node]) in _TIMING_BURST_INDICES
        pending += [(tree.children_left[node], covered), (tree.children_right[node], covered)]
    if uncovered:
        raise PathCoverageError(f"7.14: leaves {sorted(uncovered)} lack a timing/burst split")


def train(frame: pd.DataFrame, seed: int) -> TrainResult:
    """Fit the classifier tree, cross-validate it, and report on the held-out flows.

    Raises:
        FeatureFrameError: If the feature table violates the contract.
        PathCoverageError: If the fitted tree fails the 7.14 check.
    """
    validate_feature_frame(frame)
    train_part, held_out = split_held_out(frame, seed)
    features = train_part[list(FEATURE_ORDER)].to_numpy()
    labels = train_part["label"].to_numpy()
    fold_scores = []
    for fit_rows, score_rows in cross_validation_folds(train_part, seed):
        fold_model = _new_tree(seed).fit(features[fit_rows], labels[fit_rows])
        predicted = fold_model.predict(features[score_rows])
        fold_scores.append(float(f1_score(labels[score_rows], predicted, average="macro")))
    model = _new_tree(seed).fit(features, labels)
    check_path_coverage(model)
    report = {
        "seed": seed,
        "model_hash": model_hash(model),
        "depth": int(model.get_depth()),
        "leaves": int(model.get_n_leaves()),
        "cv_macro_f1": {
            "mean": float(np.mean(fold_scores)),
            "std": float(np.std(fold_scores)),
            "folds": fold_scores,
        },
        "feature_importances": dict(
            zip(FEATURE_ORDER, map(float, model.feature_importances_), strict=True)
        ),
        "held_out": _checkpoint_report(model, held_out),
    }
    return TrainResult(model, held_out, report)


def main(argv: Sequence[str] | None = None) -> int:
    """Train from a feature CSV and write model.pkl, report.json and held_out.csv.

    Returns:
        Zero on success, one if the data or the fitted tree violates the contract.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features", type=Path, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        result = train(pd.read_csv(args.features), args.seed)
    except (OSError, ValueError) as exc:
        LOGGER.error("training failed: %s", exc)
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "model.pkl").write_bytes(pickle.dumps(result.model))
    (args.out / "report.json").write_text(json.dumps(result.report, indent=2) + "\n")
    result.held_out.to_csv(args.out / "held_out.csv", index=False)
    LOGGER.info(
        "trained model %s (depth %d), CV macro-F1 %.3f",
        result.report["model_hash"][:12],
        result.report["depth"],
        result.report["cv_macro_f1"]["mean"],
    )
    return 0


def _new_tree(seed: int) -> DecisionTreeClassifier:
    return DecisionTreeClassifier(
        max_depth=TREE_MAX_DEPTH, class_weight="balanced", random_state=seed
    )


def _checkpoint_report(model: DecisionTreeClassifier, held_out: pd.DataFrame) -> dict:
    labels = [int(label) for label in TRAINING_LABELS]
    report = {}
    for checkpoint in FEATURE_CHECKPOINTS:
        rows = held_out[held_out["checkpoint"] == checkpoint]
        predicted = model.predict(rows[list(FEATURE_ORDER)].to_numpy())
        precision, recall, _, support = precision_recall_fscore_support(
            rows["label"], predicted, labels=labels, zero_division=0
        )
        report[str(checkpoint)] = {
            label.name: {
                "precision": float(precision[i]),
                "recall": float(recall[i]),
                "support": int(support[i]),
            }
            for i, label in enumerate(TRAINING_LABELS)
        }
    return report


if __name__ == "__main__":
    raise SystemExit(main())
