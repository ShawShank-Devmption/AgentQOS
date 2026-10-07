"""Generated classifier.p4 section: tables derive from contracts so P4 and entries never drift."""

import re

import pytest

from common.contracts import FEATURE_ORDER, TREE_TABLE_NAMES
from ml.compile_tree import GENERATED_BEGIN, GENERATED_END, emit_p4_declarations, splice_generated


def test_declares_every_tree_table_with_contract_key_order() -> None:
    text = emit_p4_declarations()
    tables = re.findall(r"table (\w+) \{(.*?)\n    \}", text, re.S)
    assert [name for name, _ in tables] == list(TREE_TABLE_NAMES)
    for level, (_, body) in enumerate(tables):
        keys = re.findall(r"^\s+meta\.([\w.]+) : (\w+);", body, re.M)
        assert keys == [("tree_node", "exact")] + [
            (f"features.{name}", "range") for name in FEATURE_ORDER
        ]
        assert f"size = {2 ** (level + 1)};" in body
        assert "default_action = NoAction();" in body  # miss -> class stays UNKNOWN


def test_splice_replaces_only_the_generated_section_and_is_idempotent() -> None:
    source = (
        f"control C() {{\n    {GENERATED_BEGIN}\n    old\n    {GENERATED_END}\n    apply {{}}\n}}\n"
    )
    once = splice_generated(source, emit_p4_declarations())
    assert "old" not in once
    assert once.startswith("control C() {\n") and once.endswith("    apply {}\n}\n")
    assert splice_generated(once, emit_p4_declarations()) == once


def test_splice_requires_markers() -> None:
    with pytest.raises(ValueError, match="markers"):
        splice_generated("control C() { apply {} }", emit_p4_declarations())
