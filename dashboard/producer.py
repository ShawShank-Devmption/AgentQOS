"""Produce atomic live dashboard snapshots from line-buffered tshark telemetry."""

from __future__ import annotations

import argparse
import json
import logging
import math
import time
from collections import defaultdict, deque
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
class PacketKey:
    """Stable IPv4/TCP identity used to match a packet at two taps."""

    source_ip: str
    destination_ip: str
    ip_id: int
    protocol: int
    source_port: int
    destination_port: int
    tcp_sequence: int
    tcp_acknowledgment: int
    tcp_payload_bytes: int
    tcp_flags: int
    frame_bytes: int


@dataclass(frozen=True)
class PacketTelemetry:
    """One packet row exported by the live tshark process."""

    timestamp_s: float
    source_ip: str
    destination_ip: str
    frame_bytes: int
    ack_rtt_s: float | None
    packet_key: PacketKey | None = None


def build_snapshot(
    telemetry_path: Path,
    *,
    ingress_telemetry_path: Path | None = None,
    now_s: float,
    window_s: float = DEFAULT_WINDOW_S,
) -> dict[str, object]:
    """Aggregate one sliding telemetry window into the dashboard schema.

    Args:
        telemetry_path: Tab-separated tshark packet telemetry.
        ingress_telemetry_path: Optional matching telemetry captured before the switch.
        now_s: Inclusive end of the aggregation window as Unix seconds.
        window_s: Positive sliding-window duration in seconds.

    Returns:
        A dashboard snapshot containing every frozen training class.
    """
    return build_snapshot_from_packets(
        read_telemetry(telemetry_path),
        ingress_packets=(
            read_telemetry(ingress_telemetry_path) if ingress_telemetry_path is not None else None
        ),
        now_s=now_s,
        window_s=window_s,
    )


def build_snapshot_from_packets(
    packets: Sequence[PacketTelemetry],
    *,
    ingress_packets: Sequence[PacketTelemetry] | None = None,
    now_s: float,
    window_s: float = DEFAULT_WINDOW_S,
) -> dict[str, object]:
    """Aggregate already-parsed packet telemetry into the dashboard schema.

    Args:
        packets: Validated packet telemetry in capture order.
        ingress_packets: Optional matching packets from the sender-side switch taps.
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
        if ingress_packets is None and packet.ack_rtt_s is not None:
            latencies_ms[traffic_class].append(packet.ack_rtt_s * 1_000)

    latency_method = "tcp_ack_rtt"
    latency_match: dict[str, int | float] = {
        "eligible_packets": 0,
        "matched_packets": 0,
        "coverage": 0.0,
    }
    if ingress_packets is not None:
        latency_method = "matched_two_tap"
        latencies_ms, eligible_packets, matched_packets = _matched_latencies_ms(
            packets,
            ingress_packets,
            class_ips,
            cutoff_s=cutoff_s,
            now_s=now_s,
        )
        latency_match = {
            "eligible_packets": eligible_packets,
            "matched_packets": matched_packets,
            "coverage": matched_packets / eligible_packets if eligible_packets else 0.0,
        }

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
        "latency_method": latency_method,
        "latency_match": latency_match,
        "classes": classes,
    }


def write_snapshot(
    telemetry_path: Path,
    snapshot_path: Path,
    *,
    ingress_telemetry_path: Path | None = None,
    now_s: float | None = None,
    window_s: float = DEFAULT_WINDOW_S,
) -> None:
    """Build and atomically replace one dashboard snapshot.

    Args:
        telemetry_path: Tab-separated tshark packet telemetry.
        snapshot_path: JSON destination read by the dashboard server.
        ingress_telemetry_path: Optional matching telemetry captured before the switch.
        now_s: Optional deterministic Unix timestamp for tests.
        window_s: Positive sliding-window duration in seconds.
    """
    snapshot = build_snapshot(
        telemetry_path,
        ingress_telemetry_path=ingress_telemetry_path,
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
    parser.add_argument("--ingress-telemetry", type=Path)
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
            write_snapshot(
                args.telemetry,
                args.snapshot,
                ingress_telemetry_path=args.ingress_telemetry,
                window_s=args.window_s,
            )
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
            if len(fields) not in (5, 13):
                raise ValueError("expected five or thirteen fields")
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
            packet_key = _parse_packet_key(fields, source_ip, destination_ip, frame_bytes)
        except ValueError as exc:
            raise LiveMetricError(
                f"invalid packet telemetry on line {line_number}: {telemetry_path}"
            ) from exc
        rows.append(
            PacketTelemetry(
                timestamp_s,
                source_ip,
                destination_ip,
                frame_bytes,
                ack_rtt_s,
                packet_key,
            )
        )
    return tuple(rows)


def _parse_packet_key(
    fields: list[str],
    source_ip: str,
    destination_ip: str,
    frame_bytes: int,
) -> PacketKey | None:
    if len(fields) == 5 or not all(fields[5:]):
        return None
    values = tuple(int(value, 0) for value in fields[5:])
    ip_id, protocol, source_port, destination_port, sequence, acknowledgment, length, flags = values
    if protocol != 6:
        return None
    if not 0 <= ip_id <= 65_535:
        raise ValueError("IPv4 ID is outside uint16")
    if not 0 < source_port <= 65_535 or not 0 < destination_port <= 65_535:
        raise ValueError("TCP port is outside uint16")
    if any(value < 0 for value in (sequence, acknowledgment, length, flags)):
        raise ValueError("TCP identity fields must be non-negative")
    return PacketKey(
        source_ip,
        destination_ip,
        ip_id,
        protocol,
        source_port,
        destination_port,
        sequence,
        acknowledgment,
        length,
        flags,
        frame_bytes,
    )


def _matched_latencies_ms(
    target_packets: Sequence[PacketTelemetry],
    ingress_packets: Sequence[PacketTelemetry],
    class_ips: Mapping[str, TrafficClass],
    *,
    cutoff_s: float,
    now_s: float,
) -> tuple[dict[TrafficClass, list[float]], int, int]:
    latencies = {traffic_class: [] for traffic_class in TRAINING_LABELS}
    eligible_packets = 0
    matched_packets = 0
    ingress_by_key: dict[PacketKey, deque[float]] = defaultdict(deque)
    for packet in sorted(ingress_packets, key=lambda item: item.timestamp_s):
        if packet.packet_key is not None:
            ingress_by_key[packet.packet_key].append(packet.timestamp_s)

    for packet in sorted(target_packets, key=lambda item: item.timestamp_s):
        if packet.timestamp_s < cutoff_s or packet.timestamp_s > now_s or packet.packet_key is None:
            continue
        source_class = class_ips.get(packet.source_ip)
        destination_class = class_ips.get(packet.destination_ip)
        traffic_class = source_class or destination_class
        if traffic_class is None:
            continue
        eligible_packets += 1
        candidates = ingress_by_key.get(packet.packet_key)
        if not candidates:
            continue
        if source_class is not None:
            if candidates[0] > packet.timestamp_s:
                continue
            ingress_time_s = candidates.popleft()
            latency_s = packet.timestamp_s - ingress_time_s
        else:
            while candidates and candidates[0] < packet.timestamp_s:
                candidates.popleft()
            if not candidates:
                continue
            source_time_s = candidates.popleft()
            latency_s = source_time_s - packet.timestamp_s
        if math.isfinite(latency_s) and latency_s >= 0:
            latencies[traffic_class].append(latency_s * 1_000)
            matched_packets += 1
    return latencies, eligible_packets, matched_packets


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
