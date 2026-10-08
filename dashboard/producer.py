"""Produce atomic live dashboard snapshots from line-buffered tshark telemetry."""

from __future__ import annotations

import argparse
import json
import logging
import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from ipaddress import IPv4Address
from pathlib import Path

from common.contracts import TRAINING_LABELS, TrafficClass
from eval.metrics import percentile_summary
from harness.demo import AGENT_SOURCE_IPS
from harness.topology import HOSTS

LOGGER = logging.getLogger(__name__)
DEFAULT_WINDOW_S = 5.0
DEFAULT_INTERVAL_S = 1.0


class LiveMetricError(ValueError):
    """Raised when live packet telemetry violates its expected schema."""


@dataclass(frozen=True)
class PacketTelemetry:
    """One packet row exported by the live tshark process."""

    timestamp_s: float
    source_ip: str
    destination_ip: str
    frame_bytes: int
    ack_rtt_s: float | None


def build_snapshot(
    telemetry_path: Path,
    *,
    now_s: float,
    window_s: float = DEFAULT_WINDOW_S,
) -> dict[str, object]:
    """Aggregate one sliding telemetry window into the dashboard schema.

    Args:
        telemetry_path: Tab-separated tshark packet telemetry.
        now_s: Inclusive end of the aggregation window as Unix seconds.
        window_s: Positive sliding-window duration in seconds.

    Returns:
        A dashboard snapshot containing every frozen training class.
    """
    return build_snapshot_from_packets(
        read_telemetry(telemetry_path),
        now_s=now_s,
        window_s=window_s,
    )


def build_snapshot_from_packets(
    packets: Sequence[PacketTelemetry],
    *,
    now_s: float,
    window_s: float = DEFAULT_WINDOW_S,
) -> dict[str, object]:
    """Aggregate already-parsed packet telemetry into the dashboard schema.

    Args:
        packets: Validated packet telemetry in capture order.
        now_s: Inclusive end of the aggregation window as Unix seconds.
        window_s: Positive sliding-window duration in seconds.

    Returns:
        A dashboard snapshot containing every frozen training class.
    """
    if not math.isfinite(now_s) or now_s < 0:
        raise ValueError("now_s must be finite and non-negative")
    if not math.isfinite(window_s) or window_s <= 0:
        raise ValueError("window_s must be finite and positive")
    class_ips = _class_ip_map()
    cutoff_s = now_s - window_s
    byte_counts = {traffic_class: 0 for traffic_class in TRAINING_LABELS}
    latencies_ms: dict[TrafficClass, list[float]] = {
        traffic_class: [] for traffic_class in TRAINING_LABELS
    }
    for packet in packets:
        if packet.timestamp_s < cutoff_s or packet.timestamp_s > now_s:
            continue
        traffic_class = class_ips.get(packet.source_ip) or class_ips.get(packet.destination_ip)
        if traffic_class is None:
            continue
        byte_counts[traffic_class] += packet.frame_bytes
        if packet.ack_rtt_s is not None:
            latencies_ms[traffic_class].append(packet.ack_rtt_s * 1_000)

    classes: dict[str, dict[str, float]] = {}
    for traffic_class in TRAINING_LABELS:
        samples = latencies_ms[traffic_class]
        if samples:
            summary = percentile_summary(samples)
            percentiles = (summary.p50_ms, summary.p95_ms, summary.p99_ms)
        else:
            percentiles = (0.0, 0.0, 0.0)
        classes[traffic_class.name] = {
            "throughput_mbps": byte_counts[traffic_class] * 8 / (window_s * 1_000_000),
            "p50_ms": percentiles[0],
            "p95_ms": percentiles[1],
            "p99_ms": percentiles[2],
        }
    return {
        "generated_at": datetime.fromtimestamp(now_s, UTC).isoformat(),
        "classes": classes,
    }


def write_snapshot(
    telemetry_path: Path,
    snapshot_path: Path,
    *,
    now_s: float | None = None,
    window_s: float = DEFAULT_WINDOW_S,
) -> None:
    """Build and atomically replace one dashboard snapshot.

    Args:
        telemetry_path: Tab-separated tshark packet telemetry.
        snapshot_path: JSON destination read by the dashboard server.
        now_s: Optional deterministic Unix timestamp for tests.
        window_s: Positive sliding-window duration in seconds.
    """
    snapshot = build_snapshot(
        telemetry_path,
        now_s=time.time() if now_s is None else now_s,
        window_s=window_s,
    )
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = snapshot_path.with_name(f"{snapshot_path.name}.tmp")
    temporary_path.write_text(
        json.dumps(snapshot, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(snapshot_path)


def main(argv: Sequence[str] | None = None) -> int:
    """Continuously update a live metric snapshot until interrupted.

    Args:
        argv: Optional command-line arguments.

    Returns:
        Zero after a clean interrupt, otherwise one for invalid input.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--telemetry", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--window-s", type=float, default=DEFAULT_WINDOW_S)
    parser.add_argument("--interval-s", type=float, default=DEFAULT_INTERVAL_S)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if not math.isfinite(args.interval_s) or args.interval_s <= 0:
        LOGGER.error("interval-s must be finite and positive")
        return 1
    try:
        while True:
            write_snapshot(args.telemetry, args.snapshot, window_s=args.window_s)
            time.sleep(args.interval_s)
    except KeyboardInterrupt:
        LOGGER.info("live metric producer stopping")
    except (FileNotFoundError, LiveMetricError, OSError, ValueError) as exc:
        LOGGER.error("live metric producer failed: %s", exc)
        return 1
    return 0


def read_telemetry(telemetry_path: Path) -> tuple[PacketTelemetry, ...]:
    """Read complete tshark telemetry rows while tolerating a partial final row.

    Args:
        telemetry_path: Tab-separated packet telemetry path.

    Returns:
        Validated packet rows in capture order.
    """
    if not telemetry_path.is_file():
        raise FileNotFoundError(telemetry_path)
    try:
        raw = telemetry_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise LiveMetricError(f"could not read packet telemetry: {telemetry_path}") from exc
    lines = raw.splitlines()
    if raw and not raw.endswith(("\n", "\r")):
        lines = lines[:-1]
    rows: list[PacketTelemetry] = []
    for line_number, line in enumerate(lines, start=1):
        if not line:
            continue
        fields = line.split("\t")
        try:
            if len(fields) != 5:
                raise ValueError("expected five fields")
            timestamp_s = float(fields[0])
            source_ip = str(IPv4Address(fields[1]))
            destination_ip = str(IPv4Address(fields[2]))
            frame_bytes = int(fields[3])
            ack_rtt_s = float(fields[4]) if fields[4] else None
            values = (timestamp_s, ack_rtt_s) if ack_rtt_s is not None else (timestamp_s,)
            if any(not math.isfinite(value) or value < 0 for value in values):
                raise ValueError("numeric values must be finite and non-negative")
            if frame_bytes <= 0:
                raise ValueError("frame length must be positive")
        except ValueError as exc:
            raise LiveMetricError(
                f"invalid packet telemetry on line {line_number}: {telemetry_path}"
            ) from exc
        rows.append(PacketTelemetry(timestamp_s, source_ip, destination_ip, frame_bytes, ack_rtt_s))
    return tuple(rows)


def _class_ip_map() -> Mapping[str, TrafficClass]:
    human_ip = next(host.ip_address.split("/", 1)[0] for host in HOSTS if host.role == "human")
    return {
        human_ip: TrafficClass.HUMAN_INTERACTIVE,
        AGENT_SOURCE_IPS[0]: TrafficClass.AGENT_INTERACTIVE,
        AGENT_SOURCE_IPS[1]: TrafficClass.AGENT_INTERACTIVE,
        AGENT_SOURCE_IPS[2]: TrafficClass.AGENT_BULK,
        AGENT_SOURCE_IPS[3]: TrafficClass.AGENT_BULK,
    }


if __name__ == "__main__":
    raise SystemExit(main())
