"""Train the depth-8 classifier tree on the integerized feature table (design.md 8 step 3; P3.6)."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import pickle
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import confusion_matrix, f1_score
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.tree import DecisionTreeClassifier

from common.contracts import (
    FEATURE_BIT_WIDTHS,
    FEATURE_CHECKPOINTS,
    FEATURE_ORDER,
    TIMING_BURST_FEATURES,
    TRAINING_LABELS,
    TREE_MAX_DEPTH,
    TrafficClass,
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
    if frame.columns.has_duplicates:
        raise FeatureFrameError("feature table contains duplicate columns")
    if frame.empty:
        raise FeatureFrameError("feature table is empty")
    if frame[list(FEATURE_FRAME_COLUMNS)].isna().any().any():
        raise FeatureFrameError("feature table contains null values")
    for name in ("flow_id", "source_framework"):
        if not frame[name].map(_valid_identifier).all():
            raise FeatureFrameError(f"{name} must contain nonempty string identifiers")
    if frame.duplicated(["flow_id", "checkpoint"]).any():
        raise FeatureFrameError("feature table repeats a flow checkpoint")
    for name in ("label", "source_framework"):
        if (frame.groupby("flow_id")[name].nunique() > 1).any():
            raise FeatureFrameError(f"conflicting per-flow {name}")
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


def split_held_out(
    frame: pd.DataFrame, seed: int, groups: Mapping[str, str] | None = None
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Hold out whole groups from every class, using flow IDs unless explicit groups are given.

    Each class gives HELD_OUT_FRACTION of its groups, at least one when it has two or more, so
    every class is measured on groups never trained on even when there are only a few sessions.
    A class with a single group stays in training (it cannot be both learned and tested).

    Raises:
        FeatureFrameError: If a group mixes labels or no class has two groups.
    """
    row_groups = _row_groups(frame, groups)
    group_labels = frame.groupby(row_groups.to_numpy())["label"].unique()
    if (group_labels.map(len) > 1).any():
        raise FeatureFrameError("each group must carry a single label to stratify held-out data")
    by_label: dict[int, list[str]] = {}
    for group, labels in group_labels.items():
        by_label.setdefault(int(labels[0]), []).append(str(group))
    rng = np.random.default_rng(seed)
    held: set[str] = set()
    for label in sorted(by_label):
        members = sorted(by_label[label])
        if len(members) >= 2:
            count = max(1, round(HELD_OUT_FRACTION * len(members)))
            held.update(str(group) for group in rng.choice(members, size=count, replace=False))
    if not held:
        raise FeatureFrameError("held-out splitting requires two independent groups in a class")
    held_rows = row_groups.astype(str).isin(held).to_numpy()
    return frame[~held_rows], frame[held_rows]


def cross_validation_folds(
    frame: pd.DataFrame, seed: int, groups: Mapping[str, str] | None = None
) -> Iterator[tuple[np.ndarray, np.ndarray]]:
    """Yield stratified CV row positions, keeping all rows of each explicit group together."""
    row_groups = _row_groups(frame, groups)
    if row_groups.nunique() < CV_FOLDS:
        raise FeatureFrameError(f"cross-validation requires at least {CV_FOLDS} independent groups")
    folds = StratifiedGroupKFold(n_splits=CV_FOLDS, shuffle=True, random_state=seed)
    for fit_rows, score_rows in folds.split(frame, frame["label"], groups=row_groups):
        if not len(fit_rows) or not len(score_rows):
            raise FeatureFrameError("cross-validation produced an empty group partition")
        yield fit_rows, score_rows


def check_path_coverage(model: DecisionTreeClassifier) -> None:
    """Assert every root-to-leaf path splits on a timing/burst feature (7.14).

    A leaf reachable only through size/ratio/TLS splits would classify one-way-visible flows on
    a degraded ratio alone.

    Raises:
        PathCoverageError: Naming the uncovered leaves.
    """
    uncovered = uncovered_leaves(model)
    if uncovered:
        raise PathCoverageError(f"7.14: leaves {sorted(uncovered)} lack a timing/burst split")


def uncovered_leaves(model: DecisionTreeClassifier) -> list[int]:
    """Leaves whose root-to-leaf path has no timing/burst split (7.14)."""
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
    return uncovered


def cross_validated_macro_f1(
    train_part: pd.DataFrame, seed: int, groups: Mapping[str, str] | None = None
) -> list[float]:
    """Macro-F1 of a fresh tree on each grouped, stratified CV fold of the training part."""
    features = train_part[list(FEATURE_ORDER)].to_numpy()
    labels = train_part["label"].to_numpy()
    scores = []
    for fit_rows, score_rows in cross_validation_folds(train_part, seed, groups=groups):
        fold_model = new_tree(seed).fit(features[fit_rows], labels[fit_rows])
        scores.append(
            float(
                f1_score(
                    labels[score_rows],
                    fold_model.predict(features[score_rows]),
                    labels=list(TRAINING_LABELS),
                    average="macro",
                    zero_division=0,
                )
            )
        )
    return scores


def train(frame: pd.DataFrame, seed: int, groups: Mapping[str, str] | None = None) -> TrainResult:
    """Fit the classifier tree, cross-validate it, and report on the held-out flows.

    Raises:
        FeatureFrameError: If the feature table violates the contract.
        PathCoverageError: If the fitted tree fails the 7.14 check.
    """
    validate_feature_frame(frame)
    row_groups = _row_groups(frame, groups)
    train_part, held_out = split_held_out(frame, seed, groups=groups)
    train_groups = (
        None if groups is None else {flow_id: groups[flow_id] for flow_id in train_part["flow_id"]}
    )
    fold_scores = cross_validated_macro_f1(train_part, seed, groups=train_groups)
    model = new_tree(seed).fit(
        train_part[list(FEATURE_ORDER)].to_numpy(), train_part["label"].to_numpy()
    )
    check_path_coverage(model)
    held_reports = checkpoint_reports(model, held_out)
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
        "held_out": {key: value["per_class"] for key, value in held_reports.items()},
        "held_out_confusion_matrices": {
            key: value["confusion_matrix"] for key, value in held_reports.items()
        },
        "held_out_by_source": {
            source: checkpoint_reports(model, held_out[held_out["source_framework"] == source])
            for source in sorted(frame["source_framework"].unique())
        },
        "grouping": {
            "mode": "flow_id" if groups is None else "explicit",
            "flow_count": int(frame["flow_id"].nunique()),
            "group_count": int(row_groups.nunique()),
            "training_group_count": int(_row_groups(train_part, train_groups).nunique()),
            "held_out_group_count": int(held_out["flow_id"].map(groups).nunique())
            if groups is not None
            else int(held_out["flow_id"].nunique()),
            "held_out_groups": sorted(set(held_out["flow_id"].map(groups)))
            if groups is not None
            else None,
            "mapping_sha256": _group_mapping_hash(frame, groups),
        },
    }
    return TrainResult(model, held_out, report)


def main(argv: Sequence[str] | None = None) -> int:
    """Train from a feature CSV and write model.pkl, report.json and held_out.csv.

    Returns:
        Zero on success, one if the data or the fitted tree violates the contract.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features", type=Path, required=True)
    parser.add_argument(
        "--groups", type=Path, help="Optional flow_id,group_id CSV evaluation sidecar"
    )
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        groups = None if args.groups is None else load_groups(args.groups)
        result = train(pd.read_csv(args.features), args.seed, groups=groups)
    except (OSError, ValueError) as exc:
        LOGGER.error("training failed: %s", exc)
        return 1
    if args.groups is not None:
        result.report["grouping"]["sidecar_path"] = str(args.groups)
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


def new_tree(seed: int) -> DecisionTreeClassifier:
    """The deployed tree configuration (depth <= TREE_MAX_DEPTH, class-balanced)."""
    return DecisionTreeClassifier(
        max_depth=TREE_MAX_DEPTH, class_weight="balanced", random_state=seed
    )


def _valid_identifier(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _row_groups(frame: pd.DataFrame, groups: Mapping[str, str] | None) -> pd.Series:
    validate_feature_frame(frame)
    if groups is None:
        return frame["flow_id"]
    if not all(
        _valid_identifier(key) and _valid_identifier(value) for key, value in groups.items()
    ):
        raise FeatureFrameError("group mapping must contain nonempty string flow and group IDs")
    flows = set(frame["flow_id"])
    if set(groups) != flows:
        raise FeatureFrameError("group mapping must cover exactly the feature table's flow IDs")
    return frame["flow_id"].map(groups)


def load_groups(path: Path) -> dict[str, str]:
    """Read a flow_id,group_id CSV sidecar into a complete flow-to-group mapping."""
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    if list(frame.columns) != ["flow_id", "group_id"]:
        raise FeatureFrameError("group sidecar columns must be flow_id,group_id")
    if frame.empty or frame["flow_id"].duplicated().any():
        raise FeatureFrameError("group sidecar must contain one unambiguous row per flow")
    return dict(zip(frame["flow_id"], frame["group_id"], strict=True))


def _group_mapping_hash(frame: pd.DataFrame, groups: Mapping[str, str] | None) -> str:
    pairs = sorted(
        (flow_id, flow_id if groups is None else groups[flow_id])
        for flow_id in frame["flow_id"].unique()
    )
    return hashlib.sha256(json.dumps(pairs, ensure_ascii=True).encode()).hexdigest()


def checkpoint_reports(model: DecisionTreeClassifier, held_out: pd.DataFrame) -> dict:
    """Per-checkpoint confusion matrix and per-class precision/recall on held-out rows."""
    return {
        str(checkpoint): _performance_report(model, held_out[held_out["checkpoint"] == checkpoint])
        for checkpoint in FEATURE_CHECKPOINTS
    }


def _performance_report(model: DecisionTreeClassifier, rows: pd.DataFrame) -> dict:
    labels = [int(label) for label in TRAINING_LABELS]
    predicted = (
        np.empty(0, dtype=int)
        if rows.empty
        else model.predict(rows[list(FEATURE_ORDER)].to_numpy())
    )
    counts = (
        np.zeros((len(labels), len(labels)), dtype=int)
        if rows.empty
        else confusion_matrix(rows["label"], predicted, labels=labels)
    )
    per_class = {}
    for i, label in enumerate(TRAINING_LABELS):
        support = int(counts[i, :].sum())
        predicted_count = int(counts[:, i].sum())
        per_class[label.name] = {
            "precision": float(counts[i, i] / predicted_count) if predicted_count else None,
            "recall": float(counts[i, i] / support) if support else None,
            "support": support,
        }
    agent_rows = rows["label"].isin([TrafficClass.AGENT_INTERACTIVE, TrafficClass.AGENT_BULK])
    agent_support = int(agent_rows.sum())
    agent_as_human = int((agent_rows & (predicted == TrafficClass.HUMAN_INTERACTIVE)).sum())
    return {
        "per_class": per_class,
        "confusion_matrix": {
            "labels": [label.name for label in TRAINING_LABELS],
            "counts": counts.tolist(),
            "sample_count": len(rows),
            "available": not rows.empty,
        },
        "agent_as_human": {
            "count": agent_as_human,
            "support": agent_support,
            "rate": agent_as_human / agent_support if agent_support else None,
        },
    }


if __name__ == "__main__":
    raise SystemExit(main())
