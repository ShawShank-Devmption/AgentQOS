"""Convert synchronized two-tap Ethernet pcaps and orchestration labels into measurements.

P3.12; design sections 4.4, 9, 10 and 11. Capture both directions immediately before and after
the measured path, starting before the flows begin. This adapter never infers ground truth from
DSCP: egress DSCP is only the observed prediction at an ingress-counted checkpoint.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from ipaddress import IPv4Address
from pathlib import Path
from typing import Any

from scapy.all import IP, TCP, UDP, Ether
from scapy.error import Scapy_Exception
from scapy.packet import Packet

from common.contracts import CLASS_TREATMENTS, FEATURE_CHECKPOINTS, TrafficClass
from eval.metrics import MeasurementError, _number, summarize_observations
from harness.capture import PcapInputError
from ml.extract_features import FlowKey, _pcap_packets, _read_labels

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class _TapPacket:
    identity: bytes
    flow: FlowKey
    timestamp: Decimal
    size_bytes: int
    dscp: int


def _decode_packet(packet: Packet) -> _TapPacket | None:
    if not isinstance(packet, Ether) or len(bytes(packet)) < 14:
        raise MeasurementError("capture record is not a complete Ethernet frame")
    if packet.type != 0x0800:
        if IP in packet:
            raise MeasurementError("encapsulated IPv4 is unsupported; use plain Ethernet taps")
        return None  # ARP and other non-IPv4 control frames are outside the IPv4 metric corpus.
    if not isinstance(packet.payload, IP):
        raise MeasurementError("malformed IPv4 header")
    ip = packet[IP]
    raw = bytearray(bytes(ip))
    header_size = int(ip.ihl or 0) * 4
    total_size = int(ip.len or 0)
    if ip.version != 4 or not 20 <= header_size <= 60 or not header_size <= total_size <= len(raw):
        raise MeasurementError("invalid or truncated IPv4 header/length")
    if ip.frag or ip.flags.MF:
        raise MeasurementError("fragmented IPv4 cannot be matched unambiguously")
    transport = ip.payload
    transport_size = total_size - header_size
    if ip.proto == 6 and isinstance(transport, TCP):
        if not 20 <= int(transport.dataofs or 0) * 4 <= transport_size:
            raise MeasurementError("invalid or truncated TCP header")
        checksum_offset = header_size + 16
    elif ip.proto == 17 and isinstance(transport, UDP):
        if transport_size < 8 or int(transport.len or 0) != transport_size:
            raise MeasurementError("invalid or truncated UDP datagram")
        checksum_offset = header_size + 6
    else:
        raise MeasurementError("only complete IPv4 TCP/UDP packets are supported")
    # Match network datagrams across L2 changes and QoS/ECN, hop-limit and checksum rewrites.
    raw = raw[:total_size]
    raw[1] = raw[8] = 0
    raw[10:12] = raw[checksum_offset : checksum_offset + 2] = b"\x00\x00"
    key = FlowKey(ip.src, ip.dst, int(ip.proto), int(transport.sport), int(transport.dport))
    return _TapPacket(bytes(raw), key, Decimal(str(packet.time)), len(bytes(packet)), ip.tos >> 2)


def _tap_packets(path: Path) -> list[_TapPacket]:
    result: list[_TapPacket] = []
    seen: set[bytes] = set()
    try:
        for packet in _pcap_packets(path):
            observed = _decode_packet(packet)
            if observed is None:
                continue
            if observed.identity in seen:
                raise MeasurementError(f"ambiguous duplicate packet identity in {path}")
            seen.add(observed.identity)
            result.append(observed)
    except (PcapInputError, OSError, Scapy_Exception, ValueError) as exc:
        raise MeasurementError(f"invalid capture {path}: {exc}") from exc
    return result


def _unique_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for key, value in pairs:
        if key in row:
            raise MeasurementError(f"duplicate target log field: {key}")
        row[key] = value
    return row


def _tool_calls(path: Path | None, start: float, end: float) -> list[dict[str, Any]]:
    if path is None:
        return []
    calls: list[dict[str, Any]] = []
    fields = {"source_ip", "start_time_s", "end_time_s", "status", "method", "tool"}
    try:
        with path.open(encoding="utf-8") as handle:
            for index, line in enumerate(handle, start=1):
                row = json.loads(line, object_pairs_hook=_unique_fields)
                if not isinstance(row, dict) or set(row) != fields:
                    raise MeasurementError("target log fields must match the MCP target schema")
                if not isinstance(row["source_ip"], str):
                    raise MeasurementError("target log source_ip must be an IPv4 string")
                IPv4Address(row["source_ip"])
                began = _number(row["start_time_s"], "target start_time_s")
                finished = _number(row["end_time_s"], "target end_time_s")
                if finished < began:
                    raise MeasurementError("target log end precedes start")
                status = row["status"]
                if (
                    isinstance(status, bool)
                    or not isinstance(status, int)
                    or not 100 <= status <= 599
                ):
                    raise MeasurementError("target log status must be an HTTP status integer")
                if any(
                    row[key] is not None and not isinstance(row[key], str)
                    for key in ("method", "tool")
                ):
                    raise MeasurementError("target method/tool must be strings or null")
                if row["method"] != "tools/call" or not start <= began <= end:
                    continue
                if not row["tool"]:
                    raise MeasurementError("tools/call target record must identify its tool")
                calls.append(
                    {
                        "call_id": f"target-line-{index}",
                        "start_s": began,
                        "end_s": finished if 200 <= status < 300 and finished <= end else None,
                    }
                )
    except (OSError, UnicodeError, ValueError) as exc:
        raise MeasurementError(f"invalid target log {path}: {exc}") from exc
    return calls


def observations_from_pcaps(
    ingress: Path,
    egress: Path,
    labels: Path,
    start_s: float,
    end_s: float,
    tool_log: Path | None = None,
) -> dict[str, Any]:
    """Build validated observations from real paired captures and frozen orchestration labels.

    Args:
        ingress: Classic Ethernet pcap containing both directions before the measured path.
        egress: Classic Ethernet pcap containing both directions after the measured path.
        labels: Frozen labels.csv generated by harness.capture for the ingress basename.
        start_s: Inclusive synchronized measurement-window start in seconds.
        end_s: Inclusive measurement-window end in seconds, later than start_s.
        tool_log: Optional JSONL emitted by harness.mcp_target.server.

    Returns:
        Strict eval.metrics observations. Missing or late arrivals/completions remain null.

    Raises:
        MeasurementError: For malformed evidence, ambiguous matching or inconsistent clocks.
    """
    start, end = _number(start_s, "start_s"), _number(end_s, "end_s")
    if end <= start:
        raise MeasurementError("measurement window must have positive duration")
    try:
        ground_truth = _read_labels(labels, ingress.name)
    except (OSError, PcapInputError) as exc:
        raise MeasurementError(f"invalid orchestration labels: {exc}") from exc
    sent = _tap_packets(ingress)
    received = _tap_packets(egress)
    sends_by_id = {packet.identity: packet for packet in sent}
    receipts = {packet.identity: packet for packet in received}
    for packet in received:
        original = sends_by_id.get(packet.identity)
        if original is None:
            raise MeasurementError("egress packet has no matching ingress record")
        if packet.timestamp < original.timestamp:
            raise MeasurementError("egress clock precedes matching ingress timestamp")

    dscp_classes = {treatment.dscp: label for label, treatment in CLASS_TREATMENTS.items()}
    counts: dict[FlowKey, int] = {}
    packets: list[dict[str, Any]] = []
    classifications: list[dict[str, Any]] = []
    lower, upper = Decimal(str(start)), Decimal(str(end))
    for packet in sent:
        key = packet.flow if packet.flow in ground_truth else packet.flow.reverse()
        row = ground_truth.get(key)
        # §7.20: only the independent orchestration CSV supplies truth, including both directions.
        truth = TrafficClass(int(row["label"])) if row is not None else TrafficClass.UNKNOWN
        counts[key] = counts.get(key, 0) + 1
        checkpoint = counts[key]
        if not lower <= packet.timestamp <= upper:
            continue
        receipt = receipts.get(packet.identity)
        arrived = receipt is not None and receipt.timestamp <= upper
        packets.append(
            {
                "packet_id": hashlib.sha256(packet.identity).hexdigest(),
                "traffic_class": int(truth),
                "sent_s": float(packet.timestamp),
                "received_s": float(receipt.timestamp) if arrived else None,
                "size_bytes": packet.size_bytes,
            }
        )
        # §7.9: use the actual ingress ordinal (both directions), never renumber after loss.
        if row is not None and checkpoint in FEATURE_CHECKPOINTS and arrived:
            classifications.append(
                {
                    "flow_id": row["flow_id"],
                    "checkpoint": checkpoint,
                    "truth": int(truth),
                    "predicted": int(dscp_classes.get(receipt.dscp, TrafficClass.UNKNOWN)),
                    "label_source": "orchestration",
                }
            )
    result = {
        "measurement_start_s": start,
        "measurement_end_s": end,
        "packets": packets,
        "tool_calls": _tool_calls(tool_log, start, end),
        "classifications": classifications,
    }
    summarize_observations(result)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    """Export validated measurements without overwriting an existing evidence file."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ingress", type=Path)
    parser.add_argument("egress", type=Path)
    parser.add_argument("labels", type=Path)
    parser.add_argument("--start-s", type=float, required=True)
    parser.add_argument("--end-s", type=float, required=True)
    parser.add_argument("--tool-log", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        result = observations_from_pcaps(
            args.ingress, args.egress, args.labels, args.start_s, args.end_s, args.tool_log
        )
        with args.output.open("x", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2, allow_nan=False)
            handle.write("\n")
    except (OSError, MeasurementError) as exc:
        LOGGER.error("pcap measurement extraction failed: %s", exc)
        return 1
    LOGGER.info("exported %d measured packets to %s", len(result["packets"]), args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
