"""Tests for P4.3 static footprint reporting."""

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from eval.dataplane_report import summarize_compiled_program, write_report


def test_summarize_compiled_program_counts_nested_resources() -> None:
    program = {
        "pipelines": [
            {
                "tables": [
                    {"name": "tbl_tree_l0", "max_size": 32, "actions": ["next", "label"]},
                    {"name": "tbl_class_action", "max_size": 4, "actions": ["set_qos"]},
                ],
                "register_arrays": [{"name": "reg_pkt_count", "size": 65536, "bitwidth": 32}],
            }
        ],
    }

    assert asdict(summarize_compiled_program(program)) == {
        "table_count": 2,
        "table_capacity_entries": 36,
        "action_count": 3,
        "register_count": 1,
        "register_cells": 65536,
        "register_bits": 2_097_152,
    }


def test_summarize_compiled_program_rejects_missing_register_width() -> None:
    with pytest.raises(ValueError, match="register bitwidth"):
        summarize_compiled_program({"register_arrays": [{"size": 1}]})


def test_write_report_is_sorted_json(tmp_path: Path) -> None:
    input_path = tmp_path / "compiled.json"
    output_path = tmp_path / "report.json"
    input_path.write_text(json.dumps({"tables": [], "register_arrays": []}), encoding="utf-8")

    footprint = write_report(input_path, output_path)

    assert footprint.table_count == 0
    assert json.loads(output_path.read_text(encoding="utf-8"))["register_bits"] == 0
