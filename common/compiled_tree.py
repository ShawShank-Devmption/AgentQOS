"""Loader for the section 4.5 compiled-tree JSON, shared by ml/ and controller/ (3.9 safe)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from common.contracts import (
    COMPILED_TREE_ENTRY_FIELDS,
    COMPILED_TREE_FIELDS,
    FEATURE_BIT_WIDTHS,
    FEATURE_ORDER,
    TREE_LEAF_ACTION,
    TREE_NEXT_ACTION,
    TREE_NODE_BITS,
    TREE_TABLE_NAMES,
)

_KEY_BIT_WIDTHS = (TREE_NODE_BITS, *FEATURE_BIT_WIDTHS)


class CompiledTreeError(ValueError):
    """Raised when a compiled-tree file violates the section 4.5 schema."""


@dataclass(frozen=True)
class TreeEntry:
    """One range-match entry of a tbl_tree_l* table; match_ranges follow TREE_KEY_FIELDS."""

    table: str
    match_ranges: tuple[tuple[int, int], ...]
    action: str
    action_params: tuple[int, ...]
    priority: int


@dataclass(frozen=True)
class CompiledTree:
    """A validated compiled tree ready to push or evaluate."""

    model_hash: str
    feature_order: tuple[str, ...]
    entries: tuple[TreeEntry, ...]


def load_compiled_tree(path: Path) -> CompiledTree:
    """Read and validate a compiled-tree file.

    Raises:
        CompiledTreeError: If the file is unreadable or violates the schema.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CompiledTreeError(f"cannot read compiled tree {path}: {exc}") from exc
    return parse_compiled_tree(raw)


def parse_compiled_tree(raw: object) -> CompiledTree:
    """Validate decoded compiled-tree JSON against the section 4.5 schema.

    Raises:
        CompiledTreeError: On any schema violation, including feature-order skew (7.9).
    """
    fields = _require_fields(raw, COMPILED_TREE_FIELDS, "compiled tree")
    if fields["feature_order"] != list(FEATURE_ORDER):
        raise CompiledTreeError("feature_order differs from contracts.FEATURE_ORDER (7.9)")
    if not isinstance(fields["model_hash"], str) or not fields["model_hash"]:
        raise CompiledTreeError("model_hash must be a non-empty string")
    if not isinstance(fields["entries"], list):
        raise CompiledTreeError("entries must be a list")
    entries = tuple(_parse_entry(entry) for entry in fields["entries"])
    return CompiledTree(fields["model_hash"], FEATURE_ORDER, entries)


def _parse_entry(raw: object) -> TreeEntry:
    fields = _require_fields(raw, COMPILED_TREE_ENTRY_FIELDS, "tree entry")
    if fields["table"] not in TREE_TABLE_NAMES:
        raise CompiledTreeError(f"not a tree table: {fields['table']!r}")
    if fields["action"] not in (TREE_NEXT_ACTION, TREE_LEAF_ACTION):
        raise CompiledTreeError(f"not a tree action: {fields['action']!r}")
    ranges = fields["match_ranges"]
    if not isinstance(ranges, list) or len(ranges) != len(_KEY_BIT_WIDTHS):
        raise CompiledTreeError(f"match_ranges needs {len(_KEY_BIT_WIDTHS)} [low, high] pairs")
    match_ranges = tuple(_parse_range(pair, bits) for pair, bits in zip(ranges, _KEY_BIT_WIDTHS))
    if match_ranges[0][0] != match_ranges[0][1]:
        raise CompiledTreeError("tree_node is an exact match: low must equal high")
    params = fields["action_params"]
    if not isinstance(params, list) or not all(_is_uint(param) for param in params):
        raise CompiledTreeError("action_params must be unsigned integers")
    if not _is_uint(fields["priority"]):
        raise CompiledTreeError("priority must be an unsigned integer")
    return TreeEntry(
        fields["table"], match_ranges, fields["action"], tuple(params), fields["priority"]
    )


def _require_fields(raw: object, expected: Sequence[str], what: str) -> dict:
    if not isinstance(raw, dict) or set(raw) != set(expected):
        raise CompiledTreeError(f"{what} must have exactly the fields {tuple(expected)}")
    return raw


def _parse_range(raw: object, bits: int) -> tuple[int, int]:
    if (
        not isinstance(raw, list)
        or len(raw) != 2
        or not all(_is_uint(value) for value in raw)
        or not raw[0] <= raw[1] < (1 << bits)
    ):
        raise CompiledTreeError(f"bad match range {raw!r} for a {bits}-bit field")
    return raw[0], raw[1]


def _is_uint(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0
