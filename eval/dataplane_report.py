"""Summarize table and register footprint from a compiled BMv2 JSON artifact."""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DataplaneFootprint:
    """Table and register capacity reported by a compiled P4 program."""

    table_count: int
    table_capacity_entries: int
    action_count: int
    register_count: int
    register_cells: int
    register_bits: int


def _require_mapping(value: object, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{context} must be a JSON object")
    return value


def _require_nonnegative_int(value: object, context: str) -> int:
    if not isinstance(value, int) or value < 0:
        raise ValueError(f"{context} must be a non-negative integer")
    return value


def _objects_at_key(value: object, target_key: str) -> list[Mapping[str, Any]]:
    found: list[Mapping[str, Any]] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key == target_key:
                if not isinstance(child, Sequence) or isinstance(child, (str, bytes)):
                    raise ValueError(f"{target_key} must be a JSON array")
                found.extend(_require_mapping(item, f"{target_key} entry") for item in child)
            else:
                found.extend(_objects_at_key(child, target_key))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            found.extend(_objects_at_key(child, target_key))
    return found


def summarize_compiled_program(program: Mapping[str, Any]) -> DataplaneFootprint:
    """Return the static table and register capacity of compiled BMv2 JSON.

    Args:
        program: Parsed compiler JSON with table and register-array declarations.

    Raises:
        ValueError: If a capacity or bit width is missing or malformed.
    """
    tables = _objects_at_key(program, "tables")
    registers = _objects_at_key(program, "register_arrays")
    table_capacity_entries = sum(
        _require_nonnegative_int(table.get("max_size"), "table max_size") for table in tables
    )
    action_count = sum(
        len(table.get("actions", []))
        if isinstance(table.get("actions", []), Sequence)
        and not isinstance(table.get("actions", []), (str, bytes))
        else (_ for _ in ()).throw(ValueError("table actions must be a JSON array"))
        for table in tables
    )
    register_cells = 0
    register_bits = 0
    for register in registers:
        size = _require_nonnegative_int(register.get("size"), "register size")
        bitwidth = _require_nonnegative_int(register.get("bitwidth"), "register bitwidth")
        register_cells += size
        register_bits += size * bitwidth
    return DataplaneFootprint(
        table_count=len(tables),
        table_capacity_entries=table_capacity_entries,
        action_count=action_count,
        register_count=len(registers),
        register_cells=register_cells,
        register_bits=register_bits,
    )


def write_report(input_path: Path, output_path: Path) -> DataplaneFootprint:
    """Read a compiled program and write its deterministic footprint report.

    Args:
        input_path: Compiled BMv2 JSON artifact.
        output_path: Destination JSON report path.

    Returns:
        The footprint written to ``output_path``.
    """
    try:
        raw_program = json.loads(input_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise RuntimeError(f"could not read compiled P4 artifact: {input_path}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"compiled P4 artifact is invalid JSON: {input_path}") from error
    footprint = summarize_compiled_program(_require_mapping(raw_program, "compiled P4 artifact"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(asdict(footprint), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    logger.info("wrote dataplane footprint report to %s", output_path)
    return footprint


def main() -> None:
    """Write a P4 table/register footprint report from compiled BMv2 JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="compiled BMv2 JSON artifact")
    parser.add_argument("output", type=Path, help="output report JSON path")
    arguments = parser.parse_args()
    write_report(arguments.input, arguments.output)


if __name__ == "__main__":
    main()
