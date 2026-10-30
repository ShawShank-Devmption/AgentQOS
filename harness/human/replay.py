"""Validate and replay existing pcaps at their original timing without shell interpolation."""

from __future__ import annotations

import argparse
import logging
import math
import re
import subprocess
from pathlib import Path

from scapy.all import PcapReader
from scapy.error import Scapy_Exception

from harness.capture import validate_capture

logger = logging.getLogger(__name__)
_INTERFACE = re.compile(r"[a-zA-Z0-9_][a-zA-Z0-9_.:-]{0,14}\Z")


class ReplayError(RuntimeError):
    """A trace is invalid or tcpreplay failed; diagnostics are preserved."""


def validate_trace(trace_path: Path) -> Path:
    """Require complete Ethernet pcap records with finite, monotonic packet timestamps.

    Convert pcapng to classic pcap before replay, as for feature extraction.
    """
    path = trace_path.resolve(strict=True)
    if not path.is_file():
        raise ReplayError(f"trace must be a regular file: {path}")
    count = 0
    previous = None
    try:
        validate_capture(path)
        with PcapReader(str(path)) as packets:
            for packet in packets:
                timestamp = float(packet.time)
                if not math.isfinite(timestamp) or timestamp < 0:
                    raise ReplayError("trace contains invalid timestamps")
                if previous is not None and timestamp < previous:
                    raise ReplayError("trace packet timestamps are not monotonic")
                previous = timestamp
                count += 1
    except (OSError, ValueError, Scapy_Exception, EOFError) as exc:
        raise ReplayError(f"could not read trace: {path}: {exc}") from exc
    if not count:
        raise ReplayError("trace contains no packets")
    return path


def replay_trace(trace_path: Path, interface: str, output_log: Path, timeout_s: float) -> None:
    """Run tcpreplay --multiplier 1.0 on an explicitly selected namespace interface.

    The caller owns any network namespace and packet capture. No source remapping or synthetic
    labels are introduced here; orchestration must use the trace's actual source IPs (§7.20).
    """
    if _INTERFACE.fullmatch(interface) is None:
        raise ValueError("interface must be a valid Linux interface name of at most 15 characters")
    if type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError("timeout_s must be positive and finite")
    path = validate_trace(trace_path)
    with output_log.open("xb") as output:
        try:
            result = subprocess.run(
                ["tcpreplay", "--intf1", interface, "--multiplier", "1.0", str(path)],
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=timeout_s,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ReplayError(f"tcpreplay failed; inspect {output_log}") from exc
    if result.returncode:
        raise ReplayError(f"tcpreplay exit status {result.returncode}; inspect {output_log}")
    logger.info("trace replay completed: %s on %s", path, interface)


def main() -> None:
    """Replay a validated human trace within the caller's network namespace."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path)
    parser.add_argument("--interface", required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--timeout", type=float, required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    replay_trace(args.trace, args.interface, args.log, args.timeout)


if __name__ == "__main__":
    main()
