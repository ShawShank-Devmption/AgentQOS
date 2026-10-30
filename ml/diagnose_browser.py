"""P4.6 offline diagnostics on frozen features; never exports or selects a deployed model."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import confusion_matrix, f1_score
from sklearn.model_selection import GroupShuffleSplit, StratifiedGroupKFold
from sklearn.tree import DecisionTreeClassifier

from common.contracts import FEATURE_CHECKPOINTS, FEATURE_ORDER, TRAINING_LABELS, TREE_MAX_DEPTH
from ml.train import validate_feature_frame

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiagnosticConfig:
    """Local diagnostic settings, separate from the frozen §4.6 experiment schema."""

    kind: str
    seeds: tuple[int, ...]
    cv_folds: int
    held_out_fraction: float
    tree_max_depth: int
    random_forest_trees: int
    feature_sets: dict[str, tuple[str, ...]]
    profiles: tuple[str, ...]
    browser_profiles: tuple[str, ...]


def parse_config(raw: dict) -> DiagnosticConfig:
    """Validate a complete local diagnostic configuration without silently ignored keys."""
    if not isinstance(raw, dict) or set(raw) != set(DiagnosticConfig.__annotations__):
        raise ValueError("invalid diagnostic config fields")
    seeds = raw["seeds"]
    if (
        not isinstance(seeds, list)
        or not 3 <= len(seeds) <= 5
        or any(type(seed) is not int or seed < 0 or seed >= 2**32 for seed in seeds)
        or len(set(seeds)) != len(seeds)
    ):
        raise ValueError("seeds must contain 3-5 unique uint32 integers")
    for key, minimum, maximum in [
        ("cv_folds", 2, 10),
        ("tree_max_depth", 1, TREE_MAX_DEPTH),
        ("random_forest_trees", 0, 500),
    ]:
        if type(raw[key]) is not int or not minimum <= raw[key] <= maximum:
            raise ValueError(f"invalid {key}")
    fraction = raw["held_out_fraction"]
    if type(fraction) not in (float, int) or not 0 < fraction < 1:
        raise ValueError("held_out_fraction must be between zero and one")
    if raw["kind"] != "local_browser_diagnosis_v1":
        raise ValueError("unsupported diagnostic kind")
    feature_sets = raw["feature_sets"]
    if not isinstance(feature_sets, dict) or feature_sets.get("full") != list(FEATURE_ORDER):
        raise ValueError("full feature set must match the frozen feature order")
    for name, features in feature_sets.items():
        if (
            not isinstance(name, str)
            or not name.strip()
            or not isinstance(features, list)
            or not features
            or any(feature not in FEATURE_ORDER for feature in features)
            or len(set(features)) != len(features)
        ):
            raise ValueError("feature sets may contain only unique frozen features")
    for key in ("profiles", "browser_profiles"):
        values = raw[key]
        if (
            not isinstance(values, list)
            or not values
            or any(not isinstance(value, str) or not value.strip() for value in values)
            or len(set(values)) != len(values)
        ):
            raise ValueError(f"invalid {key}")
    return DiagnosticConfig(
        **{
            **raw,
            "seeds": tuple(seeds),
            "feature_sets": {k: tuple(v) for k, v in feature_sets.items()},
            "profiles": tuple(raw["profiles"]),
            "browser_profiles": tuple(raw["browser_profiles"]),
        }
    )


def feature_overlap(frame: pd.DataFrame, features: Sequence[str]) -> dict:
    """Count observed opposite-label feature aliases, not a population Bayes-error bound."""
    report = {}
    for checkpoint in FEATURE_CHECKPOINTS:
        rows = frame[frame["checkpoint"] == checkpoint]
        conflicting_vectors, conflicting_rows, errors = 0, 0, 0
        for _, group in rows.groupby(list(features), sort=False):
            counts = group["label"].value_counts()
            if len(counts) > 1:
                conflicting_vectors += 1
                conflicting_rows += len(group)
                errors += int(counts.sum() - counts.max())
        report[str(checkpoint)] = {
            "rows": len(rows),
            "conflicting_vectors": conflicting_vectors,
            "conflicting_rows": conflicting_rows,
            "observed_min_errors": errors,
            "observed_deterministic_accuracy_ceiling": 1 - errors / len(rows)
            if len(rows)
            else None,
        }
    return report


def _summary(rows: pd.DataFrame, predicted: np.ndarray) -> dict:
    labels = [int(label) for label in TRAINING_LABELS]
    counts = (
        confusion_matrix(rows["label"], predicted, labels=labels).tolist()
        if len(rows)
        else [[0 for _ in labels] for _ in labels]
    )
    per_class = {}
    for index, label in enumerate(TRAINING_LABELS):
        support = sum(counts[index])
        predicted_support = sum(row[index] for row in counts)
        per_class[label.name] = {
            "support": support,
            "precision": counts[index][index] / predicted_support if predicted_support else None,
            "recall": counts[index][index] / support if support else None,
        }
    agent_rows = rows["label"].to_numpy() != int(TRAINING_LABELS[0])
    agent_as_human = int(np.sum(agent_rows & (predicted == int(TRAINING_LABELS[0]))))
    return {
        "rows": len(rows),
        "confusion_matrix": {"labels": labels, "counts": counts},
        "per_class": per_class,
        "accuracy": float(np.mean(rows["label"].to_numpy() == predicted)) if len(rows) else None,
        "macro_f1": float(
            f1_score(rows["label"], predicted, labels=labels, average="macro", zero_division=0)
        )
        if len(rows)
        else None,
        "agent_as_human": {
            "count": agent_as_human,
            "support": int(agent_rows.sum()),
            "rate": agent_as_human / agent_rows.sum() if agent_rows.any() else None,
        },
    }


def _checkpoint_metrics(frame: pd.DataFrame, predicted: np.ndarray) -> dict:
    return {
        str(cp): _summary(
            frame[frame["checkpoint"] == cp], predicted[(frame["checkpoint"] == cp).to_numpy()]
        )
        for cp in FEATURE_CHECKPOINTS
    }


def _metrics(frame: pd.DataFrame, predicted: np.ndarray, profiles: Sequence[str]) -> dict:
    by_profile = {}
    for profile in profiles:
        mask = (frame["source_framework"] == profile).to_numpy()
        rows, subset_predictions = frame[mask], predicted[mask]
        by_profile[profile] = {
            **_summary(rows, subset_predictions),
            "checkpoints": _checkpoint_metrics(rows, subset_predictions),
        }
    return {
        "all": _summary(frame, predicted),
        "checkpoints": _checkpoint_metrics(frame, predicted),
        "profiles": by_profile,
    }


def _audit(frame: pd.DataFrame, groups: pd.Series, fit: np.ndarray, score: np.ndarray) -> dict:
    shared_flows = set(frame.iloc[fit]["flow_id"]) & set(frame.iloc[score]["flow_id"])
    shared_groups = set(groups.iloc[fit]) & set(groups.iloc[score])
    if shared_flows:
        raise ValueError("flow leakage across diagnostic split")
    return {
        "fit_rows": len(fit),
        "score_rows": len(score),
        "fit_flows": frame.iloc[fit]["flow_id"].nunique(),
        "score_flows": frame.iloc[score]["flow_id"].nunique(),
        "fit_groups": groups.iloc[fit].nunique(),
        "score_groups": groups.iloc[score].nunique(),
        "shared_flows": len(shared_flows),
        "shared_groups": len(shared_groups),
    }


def _model(
    config: DiagnosticConfig, seed: int, forest: bool = False
) -> DecisionTreeClassifier | RandomForestClassifier:
    if forest:
        return RandomForestClassifier(
            n_estimators=config.random_forest_trees,
            class_weight="balanced",
            random_state=seed,
            n_jobs=1,
        )
    return DecisionTreeClassifier(
        max_depth=config.tree_max_depth, class_weight="balanced", random_state=seed
    )


def _cross_validate(
    frame: pd.DataFrame,
    groups: pd.Series,
    config: DiagnosticConfig,
    seed: int,
    profiles: Sequence[str],
) -> list[dict]:
    results = []
    for grouping, split_groups in [("flow", frame["flow_id"]), ("session", groups)]:
        if split_groups.nunique() < config.cv_folds:
            raise ValueError("not enough development groups for CV")
        splitter = StratifiedGroupKFold(n_splits=config.cv_folds, shuffle=True, random_state=seed)
        folds = list(splitter.split(frame, frame["label"], groups=split_groups))
        audits = [_audit(frame, groups, fit, score) for fit, score in folds]
        if grouping == "session" and any(audit["shared_groups"] for audit in audits):
            raise ValueError("session leakage across CV split")
        candidates = [(name, features, False) for name, features in config.feature_sets.items()]
        if config.random_forest_trees:
            candidates.append(("full", FEATURE_ORDER, True))
        for name, features, forest in candidates:
            matrix = frame[list(features)].to_numpy()
            predicted = np.zeros(len(frame), dtype=int)
            for fit, score in folds:
                model = _model(config, seed, forest)
                model.fit(matrix[fit], frame.iloc[fit]["label"])
                predicted[score] = model.predict(matrix[score])
            results.append(
                {
                    "grouping": grouping,
                    "feature_set": name,
                    "model": "random_forest_diagnostic_only" if forest else "depth_limited_tree",
                    "features": list(features),
                    "splits": audits,
                    "metrics": _metrics(frame, predicted, profiles),
                }
            )
    return results


def diagnose(frame: pd.DataFrame, groups: Mapping[str, str], config: DiagnosticConfig) -> dict:
    """Reserve session-held-out rows and compare predeclared diagnostics only on development CV."""
    validate_feature_frame(frame)
    if set(groups) != set(frame["flow_id"]) or any(
        not isinstance(key, str) or not isinstance(value, str) or not value.strip()
        for key, value in groups.items()
    ):
        raise ValueError("group mapping must cover exactly the feature flow IDs with string groups")
    sessions = frame["flow_id"].map(groups)
    if sessions.nunique() < config.cv_folds + 1:
        raise ValueError("not enough groups for held-out and development CV")
    profiles = sorted(set(config.profiles) | set(frame["source_framework"]))
    report = {
        "config": asdict(config),
        "features": list(FEATURE_ORDER),
        "scope": "offline diagnostic; synthetic profiles are proxies, not real-world evidence",
        "overlap_interpretation": (
            "Observed deterministic-fit errors only; not a population Bayes bound. "
            "Zero exact collisions does not establish separability."
        ),
        "rows": len(frame),
        "flows": frame["flow_id"].nunique(),
        "sessions": sessions.nunique(),
        "overlap": feature_overlap(frame, FEATURE_ORDER),
        "browser_overlap": feature_overlap(
            frame[frame["source_framework"].isin(config.browser_profiles)], FEATURE_ORDER
        ),
        "runs": [],
    }
    for seed in config.seeds:
        splitter = GroupShuffleSplit(
            n_splits=1, test_size=config.held_out_fraction, random_state=seed
        )
        fit, score = next(splitter.split(frame, groups=sessions))
        audit = _audit(frame, sessions, fit, score)
        if audit["shared_groups"]:
            raise ValueError("session leakage across held-out split")
        development, held_out = frame.iloc[fit], frame.iloc[score]
        baseline = _model(config, seed)
        baseline.fit(development[list(FEATURE_ORDER)].to_numpy(), development["label"])
        predicted = baseline.predict(held_out[list(FEATURE_ORDER)].to_numpy())
        report["runs"].append(
            {
                "seed": seed,
                "held_out": {
                    "model": "predeclared_full_depth_limited_tree",
                    "split": audit,
                    "metrics": _metrics(held_out, predicted, profiles),
                },
                "cross_validation": _cross_validate(
                    development, sessions.iloc[fit], config, seed, profiles
                ),
            }
        )
    return report


def _read_groups(path: Path) -> dict[str, str]:
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    if (
        list(frame.columns) != ["flow_id", "group_id"]
        or frame["flow_id"].duplicated().any()
        or frame.eq("").any().any()
    ):
        raise ValueError("mapping CSV requires unique flow_id,group_id rows")
    return dict(frame.itertuples(index=False, name=None))


def main(argv: Sequence[str] | None = None) -> int:
    """Run the diagnostic CLI into a new evidence directory; preserve existing artifacts."""
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("features", "groups", "config", "out"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        if args.out.exists():
            raise FileExistsError(f"output already exists: {args.out}")
        config = parse_config(json.loads(args.config.read_text()))
        report = diagnose(pd.read_csv(args.features), _read_groups(args.groups), config)
        report["input_sha256"] = {
            name: hashlib.sha256(path.read_bytes()).hexdigest()
            for name, path in [
                ("features", args.features),
                ("groups", args.groups),
                ("config", args.config),
            ]
        }
        args.out.mkdir(parents=True, exist_ok=False)
        (args.out / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    except (OSError, ValueError, TypeError) as exc:
        LOGGER.error("browser diagnosis failed: %s", exc)
        return 1
    LOGGER.info("wrote browser diagnostic to %s", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
