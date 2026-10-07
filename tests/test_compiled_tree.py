"""Schema checks for the section 4.5 compiled-tree file (trust boundary for controller and ml)."""

import json
from pathlib import Path

import pytest

from common.compiled_tree import CompiledTreeError, load_compiled_tree, parse_compiled_tree
from common.contracts import FEATURE_BIT_WIDTHS, FEATURE_ORDER


def _entry(**overrides: object) -> dict:
    entry = {
        "table": "tbl_tree_l0",
        "match_ranges": [[0, 0]] + [[0, (1 << bits) - 1] for bits in FEATURE_BIT_WIDTHS],
        "action": "tree_leaf",
        "action_params": [1, 0],
        "priority": 1,
    }
    entry.update(overrides)
    return entry


def _tree(*entries: dict) -> dict:
    return {"model_hash": "abc123", "feature_order": list(FEATURE_ORDER), "entries": list(entries)}


def test_parses_valid_tree(tmp_path: Path) -> None:
    path = tmp_path / "compiled_tree.json"
    path.write_text(json.dumps(_tree(_entry())), encoding="utf-8")
    tree = load_compiled_tree(path)
    assert tree.model_hash == "abc123"
    assert tree.entries[0].match_ranges[0] == (0, 0)
    assert tree.entries[0].action_params == (1, 0)


@pytest.mark.parametrize(
    "raw",
    [
        {**_tree(), "feature_order": list(reversed(FEATURE_ORDER))},  # §7.9 skew
        {**_tree(), "extra": 1},
        _tree(_entry(table="tbl_class_action")),
        _tree(_entry(action="drop")),
        _tree(_entry(match_ranges=[[0, 1]] + [[0, 1]] * 10)),  # node must be exact
        _tree(_entry(match_ranges=[[0, 0]] + [[0, 1 << 32]] * 10)),  # wider than field
        _tree(_entry(match_ranges=[[0, 0]] * 3)),
        _tree(_entry(action_params=[True, 0])),
        _tree(_entry(priority=-1)),
    ],
)
def test_rejects_schema_violations(raw: dict) -> None:
    with pytest.raises(CompiledTreeError):
        parse_compiled_tree(raw)


def test_unreadable_file_raises(tmp_path: Path) -> None:
    with pytest.raises(CompiledTreeError, match="cannot read"):
        load_compiled_tree(tmp_path / "missing.json")
