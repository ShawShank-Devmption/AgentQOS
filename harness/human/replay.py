"""Safe command plans for human trace replay and consented browsing capture."""

from __future__ import annotations

import logging
import math
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

LOGGER = logging.getLogger(__name__)
INTERFACE_PATTERN = re.compile(r"^[A-Za-z0-9_.:@-]+$")


@dataclass(frozen=True)
class ReplayConfig:
    """Validated timing-faithful tcpreplay inputs."""

    pcap_path: Path
    interface: str
    multiplier: float = 1.0
    loops: int = 1

    def __post_init__(self) -> None:
        if not self.pcap_path.is_file():
            raise FileNotFoundError(self.pcap_path)
        _validate_interface(self.interface)
        if not math.isfinite(self.multiplier) or self.multiplier <= 0:
            raise ValueError("multiplier must be finite and positive")
        if self.loops <= 0:
            raise ValueError("loops must be positive")


@dataclass(frozen=True)
class BrowsingCaptureConfig:
    """Validated parameters for a consented browsing capture."""

    output_path: Path
    interface: str
    duration_s: int
    capture_filter: str = "tcp or udp"

    def __post_init__(self) -> None:
        _validate_interface(self.interface)
        if self.duration_s <= 0:
            raise ValueError("duration_s must be positive")
        if not self.capture_filter.strip():
            raise ValueError("capture_filter must not be empty")


def tcpreplay_command(config: ReplayConfig) -> tuple[str, ...]:
    """Return the argument vector for timing-faithful trace replay.

    Args:
        config: Validated pcap, interface, speed, and loop count.

    Returns:
        An argument vector safe for `subprocess.run` without a shell.
    """
    return (
        "tcpreplay",
        f"--intf1={config.interface}",
        f"--multiplier={config.multiplier}",
        f"--loop={config.loops}",
        str(config.pcap_path),
    )


def tshark_command(config: BrowsingCaptureConfig) -> tuple[str, ...]:
    """Return the argument vector for one bounded browsing capture.

    Args:
        config: Validated output, interface, duration, and capture filter.

    Returns:
        An argument vector safe for `subprocess.run` without a shell.
    """
    return (
        "tshark",
        "-i",
        config.interface,
        "-a",
        f"duration:{config.duration_s}",
        "-f",
        config.capture_filter,
        "-w",
        str(config.output_path),
    )


def run_replay(config: ReplayConfig) -> None:
    """Execute one timing-faithful replay and fail on tcpreplay errors."""
    _run_command(tcpreplay_command(config), "tcpreplay")


def run_browsing_capture(config: BrowsingCaptureConfig) -> None:
    """Capture one bounded, consented browsing session."""
    config.output_path.parent.mkdir(parents=True, exist_ok=True)
    _run_command(tshark_command(config), "tshark")


def _run_command(command: tuple[str, ...], name: str) -> None:
    try:
        result = subprocess.run(command, capture_output=True, check=False, text=True)
    except OSError as exc:
        raise RuntimeError(f"could not execute {name}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic output"
        raise RuntimeError(f"{name} failed: {detail}")
    LOGGER.info("%s completed", name)


def _validate_interface(interface: str) -> None:
    if not INTERFACE_PATTERN.fullmatch(interface):
        raise ValueError("interface must contain only safe interface-name characters")
