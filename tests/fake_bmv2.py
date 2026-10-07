"""In-memory stand-in for simple_switch_CLI with BMv2's duplicate-entry rule."""

from __future__ import annotations

from collections.abc import Sequence

from common.contracts import TREE_TABLE_NAMES
from controller.switch_api import SwitchApiError


class FakeBmv2:
    """Executes the CLI commands the controller emits; can crash after N commands."""

    def __init__(self, crash_after: int | None = None) -> None:
        self.tables: dict[str, dict[str, str]] = {}
        self.registers: dict[tuple[str, int], int] = {}
        self.meters: dict[tuple[str, int], str] = {}
        self.crash_after = crash_after
        self.executed = 0

    def read_register(self, name: str, index: int | None = None) -> tuple[int, ...]:
        return (self.registers.get((name, index or 0), 0),)

    def execute(self, commands: Sequence[str]) -> tuple[str, ...]:
        return tuple(self._run(command) for command in commands)

    def _run(self, command: str) -> str:
        if self.crash_after is not None and self.executed >= self.crash_after:
            raise SwitchApiError("simulated controller crash")
        self.executed += 1
        verb, *args = command.split()
        if verb == "table_clear":
            self.tables[args[0]] = {}
        elif verb == "table_add":
            table, separator = args[0], args.index("=>")
            key = " ".join(args[2:separator])
            if table in TREE_TABLE_NAMES:
                key += f" prio={args[-1]}"  # range tables key on match + priority
            entries = self.tables.setdefault(table, {})
            if key in entries:
                raise SwitchApiError(f"Invalid table operation (DUPLICATE_ENTRY): {command}")
            entries[key] = command
        elif verb == "register_write":
            self.registers[(args[0], int(args[1]))] = int(args[2])
        elif verb == "meter_set_rates":
            self.meters[(args[0], int(args[1]))] = " ".join(args[2:])
        else:
            raise SwitchApiError(f"*** Unknown syntax: {command}")
        return ""
