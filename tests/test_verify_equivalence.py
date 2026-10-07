"""Equivalence gate: sklearn tree == simulated BMv2 tables on every row (design.md 7.9; P3.7)."""

import json
import pickle
from pathlib import Path

import numpy as np
import pytest

from common.compiled_tree import parse_compiled_tree
from common.contracts import FEATURE_BIT_WIDTHS, FEATURE_ORDER
from ml.compile_tree import compile_tree
from ml.train import train
from ml.verify_equivalence import main, verify
from tests.synthetic_corpus import synthetic_feature_frame

RESULT = train(synthetic_feature_frame(seed=21), seed=21)
COMPILED = compile_tree(RESULT.model)
TREE = parse_compiled_tree(COMPILED)


def _random_vectors(count: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    columns = [
        rng.integers(0, 1 << bits, size=count, dtype=np.uint64) for bits in FEATURE_BIT_WIDTHS
    ]
    vectors = np.stack(columns, axis=1).astype(np.int64)
    vectors[:50] = [(1 << bits) - 1 for bits in FEATURE_BIT_WIDTHS]  # every field at its max
    return vectors


def _boundary_vectors() -> np.ndarray:
    """Probe each compiled split at low-1, low, high and high+1 (clamped to the field)."""
    base = RESULT.held_out[list(FEATURE_ORDER)].to_numpy()[0]
    vectors = []
    for entry in TREE.entries:
        for index, (low, high) in enumerate(entry.match_ranges[1:]):
            maximum = (1 << FEATURE_BIT_WIDTHS[index]) - 1
            if (low, high) == (0, maximum):
                continue
            for value in (low - 1, low, high, high + 1):
                if 0 <= value <= maximum:
                    probe = base.copy()
                    probe[index] = value
                    vectors.append(probe)
    return np.array(vectors)


def test_held_out_rows_agree_100_percent() -> None:
    held_out = RESULT.held_out[list(FEATURE_ORDER)].to_numpy()
    report = verify(RESULT.model, TREE, held_out)
    assert report.total == len(held_out) > 0
    assert report.mismatches == ()


def test_random_vectors_including_field_max_and_float32_range_agree() -> None:
    vectors = _random_vectors(10_000, seed=4)
    assert (vectors[:, FEATURE_ORDER.index("iat_ewma_us")] > 1 << 24).any()
    assert verify(RESULT.model, TREE, vectors).mismatches == ()


def test_split_boundaries_agree() -> None:
    vectors = _boundary_vectors()
    assert len(vectors) > 0
    assert verify(RESULT.model, TREE, vectors).mismatches == ()


def test_tampered_boundary_is_detected() -> None:
    tampered = json.loads(json.dumps(COMPILED))
    root = [e for e in tampered["entries"] if e["table"] == "tbl_tree_l0"]
    left, right = sorted(root, key=lambda e: e["match_ranges"])
    split_index = next(
        i
        for i, (a, b) in enumerate(zip(left["match_ranges"], right["match_ranges"], strict=True))
        if a != b
    )
    left["match_ranges"][split_index][1] -= 1
    right["match_ranges"][split_index][0] -= 1
    report = verify(RESULT.model, parse_compiled_tree(tampered), _boundary_vectors())
    assert report.mismatches


def test_model_and_compiled_tree_must_be_the_same_model() -> None:
    other = json.loads(json.dumps(COMPILED))
    other["model_hash"] = "0" * 64
    with pytest.raises(ValueError, match="model_hash"):
        verify(RESULT.model, parse_compiled_tree(other), _random_vectors(10, seed=1))


def test_cli_exit_codes(tmp_path: Path) -> None:
    model = tmp_path / "model.pkl"
    model.write_bytes(pickle.dumps(RESULT.model))
    compiled = tmp_path / "compiled_tree.json"
    compiled.write_text(json.dumps(COMPILED))
    held_out = tmp_path / "held_out.csv"
    RESULT.held_out.to_csv(held_out, index=False)
    report = tmp_path / "equivalence.json"
    args = ["--model", str(model), "--compiled", str(compiled), "--held-out", str(held_out)]
    assert main([*args, "--report", str(report)]) == 0
    assert json.loads(report.read_text())["mismatches"] == 0
    other = json.loads(json.dumps(COMPILED))
    other["model_hash"] = "0" * 64
    compiled.write_text(json.dumps(other))
    assert main(args) == 1
