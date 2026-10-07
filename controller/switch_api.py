"""The single controller boundary for BMv2 CLI operations."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Sequence
from pathlib import Path

from common.contracts import REGISTER_NAMES

_PROMPT = "RuntimeCmd: "
# simple_switch_CLI exits 0 even when it rejects a command; these lines are its rejections.
_REJECTION = re.compile(r"^(?:Error|Invalid \w+ operation|\*\*\* Unknown syntax)", re.MULTILINE)


class SwitchApiError(RuntimeError):
    """Raised when BMv2 rejects a controller operation."""


class SwitchApi:
    """The only controller module that talks to BMv2, via `simple_switch_CLI` sessions."""

    def __init__(self, cli_path: Path = Path("simple_switch_CLI"), thrift_port: int = 9090) -> None:
        if thrift_port <= 0 or thrift_port > 65_535:
            raise ValueError("thrift_port must be between 1 and 65535")
        self._cli_path = cli_path
        self._thrift_port = thrift_port

    def execute(self, commands: Sequence[str]) -> tuple[str, ...]:
        """Run commands in one CLI session and return each command's output.

        Args:
            commands: Single-line CLI commands, executed in order.

        Returns:
            One output string per command.

        Raises:
            ValueError: If a command spans multiple lines.
            SwitchApiError: If the CLI fails or rejects any command.
        """
        if not commands:
            return ()
        for command in commands:
            if "\n" in command:
                raise ValueError(f"CLI commands must be a single line: {command!r}")
        try:
            result = subprocess.run(
                [str(self._cli_path), "--thrift-port", str(self._thrift_port)],
                input="".join(f"{command}\n" for command in commands),
                capture_output=True,
                check=False,
                text=True,
            )
        except OSError as exc:
            raise SwitchApiError(f"could not execute BMv2 CLI: {self._cli_path}") from exc
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic output"
            raise SwitchApiError(f"BMv2 CLI failed: {detail}")
        outputs = _split_transcript(result.stdout, len(commands))
        for command, output in zip(commands, outputs):
            if _REJECTION.search(output):
                raise SwitchApiError(f"BMv2 rejected '{command}': {output.strip()}")
        return outputs

    def read_registers(self, names: Sequence[str]) -> dict[str, tuple[int, ...]]:
        """Bulk-read whole register arrays in one CLI session (design.md section 6.3).

        Raises:
            ValueError: If a name is not a contract register.
            SwitchApiError: If the CLI fails or returns an unreadable response.
        """
        for name in names:
            _require_register(name)
        outputs = self.execute([f"register_read {name}" for name in names])
        return {name: _parse_register_output(name, out) for name, out in zip(names, outputs)}

    def read_register(self, register_name: str, index: int | None = None) -> tuple[int, ...]:
        """Read one register array, or one cell of it, by contract name.

        Raises:
            ValueError: If the register name or index is invalid.
            SwitchApiError: If the CLI fails or returns an unreadable response.
        """
        _require_register(register_name)
        if index is not None and index < 0:
            raise ValueError("register index must be non-negative")
        command = f"register_read {register_name}"
        if index is not None:
            command = f"{command} {index}"
        (output,) = self.execute([command])
        return _parse_register_output(register_name, output)


def _require_register(name: str) -> None:
    if name not in REGISTER_NAMES:
        raise ValueError(f"unknown contract register: {name}")


def _split_transcript(stdout: str, command_count: int) -> tuple[str, ...]:
    # The banner precedes the first prompt; each later prompt is followed by one output.
    outputs = stdout.split(_PROMPT)[1 : 1 + command_count]
    if len(outputs) != command_count:
        raise SwitchApiError(
            f"BMv2 CLI transcript had {len(outputs)} outputs for {command_count} commands"
        )
    return tuple(outputs)


def _parse_register_output(register_name: str, output: str) -> tuple[int, ...]:
    assignment = re.compile(rf"^{re.escape(register_name)}(?:\[\d+\])?\s*=\s*(.*)$")
    values: list[int] = []
    for raw_line in output.splitlines():
        match = assignment.match(raw_line.strip())
        if match is None:
            continue
        for token in match.group(1).split(","):
            value = token.strip()
            if value:
                try:
                    values.append(int(value, 0))
                except ValueError as exc:
                    raise SwitchApiError(
                        f"BMv2 returned a non-integer value for {register_name}: {value}"
                    ) from exc

    if not values:
        raise SwitchApiError(f"BMv2 response did not contain register {register_name}")
    return tuple(values)
