"""The single controller boundary for BMv2 CLI operations."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from common.contracts import REGISTER_NAMES


class SwitchApiError(RuntimeError):
    """Raised when BMv2 rejects a controller operation."""


class SwitchApi:
    """Small BMv2 Thrift CLI adapter used by the M1 controller probe."""

    def __init__(self, cli_path: Path = Path("simple_switch_CLI"), thrift_port: int = 9090) -> None:
        if thrift_port <= 0 or thrift_port > 65_535:
            raise ValueError("thrift_port must be between 1 and 65535")
        self._cli_path = cli_path
        self._thrift_port = thrift_port

    def read_register(self, register_name: str, index: int | None = None) -> tuple[int, ...]:
        """Read a frozen register by name through `simple_switch_CLI`.

        Args:
            register_name: Name declared by the design section 4 contract.
            index: Optional non-negative register index. Omit it to read the full array.

        Returns:
            Register values in switch response order.

        Raises:
            ValueError: If the register name or index is invalid.
            SwitchApiError: If the CLI fails or returns an unreadable response.
        """
        if register_name not in REGISTER_NAMES:
            raise ValueError(f"unknown contract register: {register_name}")
        if index is not None and index < 0:
            raise ValueError("register index must be non-negative")

        command = f"register_read {register_name}"
        if index is not None:
            command = f"{command} {index}"

        try:
            result = subprocess.run(
                [str(self._cli_path), "--thrift-port", str(self._thrift_port)],
                input=f"{command}\n",
                capture_output=True,
                check=False,
                text=True,
            )
        except OSError as exc:
            raise SwitchApiError(f"could not execute BMv2 CLI: {self._cli_path}") from exc

        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic output"
            raise SwitchApiError(f"BMv2 register read failed: {detail}")

        return _parse_register_output(register_name, result.stdout)


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
