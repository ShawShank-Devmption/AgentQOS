"""What does the 7.14 timing/burst rule cost on a corpus? Diagnostic only; nothing here deploys.

On the same session-held-out split and grouped cross-validation as ml.train, fits:
  unconstrained - the deployed tree configuration without the 7.14 check
  timing_only   - the same tree, able to split only on timing/burst features (7.14 holds by
                  construction)
and reports held-out precision/recall per class and checkpoint, CV macro-F1, feature importances,
and how many unconstrained leaves 7.14 would reject. The gap between the two says whether the rule
or the data limits accuracy (P3.6/P4.6).
"""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import numpy as np
import pandas as pd

from common.contracts import FEATURE_ORDER, TIMING_BURST_FEATURES
from ml.train import (
    FeatureFrameError,
    checkpoint_reports,
    cross_validated_macro_f1,
    load_groups,
    new_tree,
    split_held_out,
    uncovered_leaves,
    validate_feature_frame,
)

LOGGER = logging.getLogger(__name__)
STATIC_FEATURES = tuple(name for name in FEATURE_ORDER if name not in TIMING_BURST_FEATURES)


def timing_only(frame: pd.DataFrame) -> pd.DataFrame:
    """Copy of `frame` whose static features are constant, so no tree can split on them."""
    view = frame.copy()
    view[list(STATIC_FEATURES)] = 0
    return view


def _macro_f1(confusion: dict) -> float | None:
    """Macro-F1 over the classes present in the held-out rows (absent classes are not scored)."""
    counts = np.array(confusion["counts"])
    scores = []
    for i in range(len(counts)):
        support, predicted = counts[i, :].sum(), counts[:, i].sum()
        if support:
            hit = counts[i, i]
            scores.append(2 * hit / (support + predicted) if hit else 0.0)
    return float(np.mean(scores)) if scores else None


def evaluate(
    frame: pd.DataFrame, seed: int, groups: Mapping[str, str] | None = None
) -> dict[str, object]:
    """Fit and score both models on one shared split.

    Raises:
        FeatureFrameError: If the table, grouping or split violates the training contract.
    """
    validate_feature_frame(frame)
    train_part, held_out = split_held_out(frame, seed, groups=groups)
    train_groups = None if groups is None else {f: groups[f] for f in train_part["flow_id"]}
    views: dict[str, Callable[[pd.DataFrame], pd.DataFrame]] = {
        "unconstrained": lambda part: part,
        "timing_only": timing_only,
    }
    models = {}
    for name, view in views.items():
        fit_part, test_part = view(train_part), view(held_out)
        model = new_tree(seed).fit(
            fit_part[list(FEATURE_ORDER)].to_numpy(), fit_part["label"].to_numpy()
        )
        folds = cross_validated_macro_f1(fit_part, seed, groups=train_groups)
        reports = checkpoint_reports(model, test_part)
        models[name] = {
            "cv_macro_f1": {"mean": float(np.mean(folds)), "folds": folds},
            "held_out_macro_f1": {
                checkpoint: _macro_f1(report["confusion_matrix"])
                for checkpoint, report in reports.items()
            },
            "held_out": {checkpoint: report["per_class"] for checkpoint, report in reports.items()},
            "feature_importances": dict(
                zip(FEATURE_ORDER, map(float, model.feature_importances_), strict=True)
            ),
            "leaves": int(model.get_n_leaves()),
            "leaves_failing_7_14": len(uncovered_leaves(model)),
        }
    return {
        "seed": seed,
        "held_out_groups": sorted(set(held_out["flow_id"].map(groups)))
        if groups is not None
        else None,
        "models": models,
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Write the comparison JSON to a new file; returns 1 on contract violations."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--features", type=Path, required=True)
    parser.add_argument("--groups", type=Path, help="flow_id,group_id CSV (sessions)")
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        groups = None if args.groups is None else load_groups(args.groups)
        result = evaluate(pd.read_csv(args.features), args.seed, groups=groups)
        with args.out.open("x", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2)
    except (OSError, ValueError, FeatureFrameError) as exc:
        LOGGER.error("rule-cost diagnostic failed: %s", exc)
        return 1
    for name, model in result["models"].items():
        LOGGER.info(
            "%s: CV macro-F1 %.3f, held-out macro-F1 %s, leaves failing 7.14: %d/%d",
            name,
            model["cv_macro_f1"]["mean"],
            {k: None if v is None else round(v, 3) for k, v in model["held_out_macro_f1"].items()},
            model["leaves_failing_7_14"],
            model["leaves"],
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
