"""Per-flow feature extraction mirroring the P4 feature stage (design.md 5.2, 8.2; P2.4/P3.3)."""

from __future__ import annotations

import argparse
import csv
import logging
import struct
from collections.abc import Iterator, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from decimal import Decimal
from ipaddress import IPv4Address
from pathlib import Path

import pandas as pd
from scapy.all import IP, TCP, UDP, Ether, PcapReader
from scapy.packet import Packet

from common.contracts import (
    CM_SKETCH_COUNTER_BITS,
    CM_SKETCH_ROWS,
    FEATURE_CHECKPOINTS,
    FEATURE_ORDER,
    FIRST_SIZE_COUNT,
    IDLE_TIMEOUT_US,
    LABEL_FIELDS,
    SKETCH_EPOCH_US,
    TRAINING_LABELS,
    AlpnClass,
    TrafficClass,
)
from common.feature_math import (
    first8_size_bucket,
    flow_age_ms,
    flow_key_bytes,
    flow_slot,
    flow_tag,
    iat_update,
    mean_pkt_size_up,
    parse_client_hello_meta,
    saturate,
    sketch_columns,
    timestamp_delta_us,
    updown_ratio_x100,
)
from harness.capture import PcapInputError, validate_capture

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PacketObservation:
    """One packet of a flow as the switch sees it."""

    timestamp_us: int
    size_bytes: int
    upstream: bool
    payload: bytes = b""


@dataclass
class FlowState:
    """Mirror of one flow slot's registers after the latest packet."""

    first_ts_us: int = 0
    last_ts_us: int = 0
    iat_ewma: int = 0
    iat_var_ewma: int = 0
    pkt_count: int = 0
    bytes_up: int = 0
    bytes_down: int = 0
    first_sizes: list[int] = field(default_factory=list)
    tls_ext_count: int = 0
    tls_alpn_class: AlpnClass = AlpnClass.NONE
    payload_seen: bool = False


def update_flow(state: FlowState, packet: PacketObservation) -> None:
    """Apply one packet to the slot state exactly as features.p4 does.

    Args:
        state: Slot state, mutated in place.
        packet: The packet being processed.
    """
    if state.pkt_count == 0:
        state.first_ts_us = packet.timestamp_us
    else:
        delta = timestamp_delta_us(packet.timestamp_us, state.last_ts_us)
        # §7.12: zero gaps are skipped; the first non-zero gap seeds the mean.
        if delta > 0 and state.iat_ewma == 0:
            state.iat_ewma = delta
        elif delta > 0:
            state.iat_ewma, state.iat_var_ewma = iat_update(
                state.iat_ewma, state.iat_var_ewma, delta
            )
    state.last_ts_us = packet.timestamp_us
    if len(state.first_sizes) < FIRST_SIZE_COUNT:
        state.first_sizes.append(packet.size_bytes)
    # §7.3: every counter saturates instead of wrapping.
    state.pkt_count = saturate(state.pkt_count + 1, 32)
    if packet.upstream:
        state.bytes_up = saturate(state.bytes_up + packet.size_bytes, 32)
    else:
        state.bytes_down = saturate(state.bytes_down + packet.size_bytes, 32)
    if packet.payload and not state.payload_seen:
        state.payload_seen = True
        state.tls_ext_count, state.tls_alpn_class = parse_client_hello_meta(packet.payload)


def feature_vector(state: FlowState, fanout_new_flows: int) -> tuple[int, ...]:
    """Return the frozen section 4.3 vector for the slot's current state, in FEATURE_ORDER."""
    return (
        state.iat_ewma,
        state.iat_var_ewma,
        state.pkt_count,
        mean_pkt_size_up(state.bytes_up, state.pkt_count),
        updown_ratio_x100(state.bytes_up, state.bytes_down),
        first8_size_bucket(state.first_sizes),
        saturate(fanout_new_flows, 16),
        state.tls_ext_count,
        int(state.tls_alpn_class),
        flow_age_ms(state.last_ts_us, state.first_ts_us),
    )


def checkpoint_vectors(
    packets: Sequence[PacketObservation], fanout_new_flows: int
) -> dict[int, tuple[int, ...]]:
    """Return the feature vector seen by the classifier at each reached checkpoint (6/16/64).

    Args:
        packets: The flow's packets in arrival order.
        fanout_new_flows: Sketch estimate for the flow's client at classification time.

    Returns:
        Checkpoint packet count mapped to the vector after that packet was processed.
    """
    state = FlowState()
    vectors: dict[int, tuple[int, ...]] = {}
    for index, packet in enumerate(packets, start=1):
        update_flow(state, packet)
        if index in FEATURE_CHECKPOINTS:
            vectors[index] = feature_vector(state, fanout_new_flows)
    return vectors


@dataclass(frozen=True)
class FlowKey:
    """Canonical bidirectional key with the orchestrated client endpoint first."""

    client_ip: str
    server_ip: str
    proto: int
    client_port: int
    server_port: int

    def reverse(self) -> FlowKey:
        """Return the opposite direction for capture lookup."""
        return FlowKey(
            self.server_ip, self.client_ip, self.proto, self.server_port, self.client_port
        )

    def wire_bytes(self) -> bytes:
        """Return the exact canonical key used by slot/tag/sketch hashing."""
        return flow_key_bytes(
            int(IPv4Address(self.client_ip)),
            int(IPv4Address(self.server_ip)),
            self.proto,
            self.client_port,
            self.server_port,
        )


@dataclass
class CmSketch:
    """Sparse offline active-epoch sketch using the frozen row hashes.

    Offline epochs start at the capture's first eligible packet (G17); actual controller epoch
    phase and BMv2 hash equivalence still require the shared PTF replay.
    """

    origin_us: int | None = None
    _epoch: int = 0
    _rows: tuple[dict[int, int], ...] = field(
        default_factory=lambda: tuple({} for _ in range(CM_SKETCH_ROWS))
    )

    def claim(self, source_ip: int, now_us: int) -> None:
        """Increment each row on a new slot claim, saturating instead of wrapping."""
        self._advance(now_us)
        for row, column in zip(self._rows, sketch_columns(source_ip), strict=True):
            row[column] = saturate(row.get(column, 0) + 1, CM_SKETCH_COUNTER_BITS)

    def estimate(self, source_ip: int, now_us: int) -> int:
        """Return the minimum active-epoch row estimate for a client IP."""
        self._advance(now_us)
        return min(
            row.get(column, 0)
            for row, column in zip(self._rows, sketch_columns(source_ip), strict=True)
        )

    def _advance(self, now_us: int) -> None:
        if now_us < 0:
            raise ValueError("sketch timestamp must be non-negative")
        if self.origin_us is None:
            self.origin_us = now_us
        epoch = (now_us - self.origin_us) // SKETCH_EPOCH_US
        if epoch < self._epoch:
            raise PcapInputError("capture timestamps must be nondecreasing")
        if epoch != self._epoch:
            # §7.10: the offline reference exposes only the new active epoch.
            for row in self._rows:
                row.clear()
            self._epoch = epoch


def replay_with_switch_timestamps(
    packets: Sequence[PacketObservation], timestamps_us: Sequence[int]
) -> list[FlowState]:
    """Return independent per-packet register states using measured switch timestamps.

    This aligns the two clocks for the P3.3 bit-exactness check; it does not itself claim that
    the missing PTF/hardware comparison has passed.
    """
    if len(packets) != len(timestamps_us) or any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in timestamps_us
    ):
        raise ValueError("one non-negative integer switch timestamp is required per packet")
    state = FlowState()
    snapshots = []
    for packet, timestamp in zip(packets, timestamps_us, strict=True):
        update_flow(
            state, PacketObservation(timestamp, packet.size_bytes, packet.upstream, packet.payload)
        )
        snapshots.append(deepcopy(state))
    return snapshots


def extract_flows(pcap: Path) -> dict[FlowKey, list[PacketObservation]]:
    """Read ordered Ethernet pcap packets into canonical bidirectional flows.

    With no label file, the first observed endpoint is the initiator. Dataset extraction instead
    uses the orchestration label to resolve direction even when a response is captured first.
    Non-IPv4 packets, non-initial fragments and missing transport headers are skipped (§7.15).
    """
    flows: dict[FlowKey, list[PacketObservation]] = {}
    for packet in _pcap_packets(pcap):
        key = _packet_key(packet)
        if key is None:
            continue
        canonical = key.reverse() if key.reverse() in flows else key
        flows.setdefault(canonical, []).append(_observation(packet, key == canonical))
    return flows


def extract_features(pcap: Path, labels_csv: Path) -> pd.DataFrame:
    """Join orchestration-only labels to slot-valid checkpoints from a real capture.

    All eligible flows, including unlabeled background, contribute to slot ownership and fanout.
    Live collisions leave the owner untouched and emit no invalid training vector (§7.1).
    The returned diagnostics report exclusions; class 0 is never invented as a training label.
    """
    labels = _read_labels(labels_csv, pcap.name)
    slots: dict[int, tuple[int, FlowState]] = {}
    directions: dict[FlowKey, FlowKey] = {}
    sketch = CmSketch()
    rows: list[dict[str, object]] = []
    collisions = 0
    for packet in _pcap_packets(pcap):
        key = _packet_key(packet)
        if key is None:
            continue
        canonical = _canonical_key(key, labels, directions)
        observation = _observation(packet, key == canonical)
        wire_key = canonical.wire_bytes()
        slot, tag = flow_slot(wire_key), flow_tag(wire_key)
        owner = slots.get(slot)
        if owner is not None and owner[0] != tag:
            if timestamp_delta_us(observation.timestamp_us, owner[1].last_ts_us) <= IDLE_TIMEOUT_US:
                collisions += 1
                continue  # §7.1: colliders must never corrupt a live owner's state.
            owner = None
        source = int(IPv4Address(canonical.client_ip))
        if owner is None:
            # §7.11: replace the entire register state before assigning a reused slot.
            owner = tag, FlowState()
            slots[slot] = owner
            sketch.claim(source, observation.timestamp_us)
        state = owner[1]
        update_flow(state, observation)
        label = labels.get(canonical)
        if label is not None and state.pkt_count in FEATURE_CHECKPOINTS:
            vector = feature_vector(state, sketch.estimate(source, observation.timestamp_us))
            rows.append(
                {
                    "flow_id": label["flow_id"],
                    "checkpoint": state.pkt_count,
                    **dict(zip(FEATURE_ORDER, vector, strict=True)),
                    "label": int(label["label"]),
                    "source_framework": label["source_framework"],
                }
            )
    columns = ["flow_id", "checkpoint", *FEATURE_ORDER, "label", "source_framework"]
    frame = pd.DataFrame(rows, columns=columns)
    frame.attrs["collision_packets"] = collisions
    if collisions:
        LOGGER.warning("excluded %d packets colliding with live feature slots", collisions)
    return frame


def _canonical_key(
    key: FlowKey, labels: dict[FlowKey, dict[str, str]], directions: dict[FlowKey, FlowKey]
) -> FlowKey:
    if key in labels:
        canonical = key
    elif key.reverse() in labels:
        canonical = key.reverse()
    else:
        canonical = directions.get(key, directions.get(key.reverse(), key))
    directions[key] = canonical
    return canonical


def _read_labels(path: Path, pcap_name: str) -> dict[FlowKey, dict[str, str]]:
    labels: dict[FlowKey, dict[str, str]] = {}
    identifiers: set[str] = set()
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if tuple(reader.fieldnames or ()) != LABEL_FIELDS:
                raise PcapInputError("labels.csv header must exactly match LABEL_FIELDS")
            for row in reader:
                if None in row or any(
                    not isinstance(value, str) or not value for value in row.values()
                ):
                    raise PcapInputError("label rows must contain every frozen field")
                try:
                    label = TrafficClass(int(row["label"]))
                    key = FlowKey(
                        str(IPv4Address(row["src_ip"])),
                        str(IPv4Address(row["dst_ip"])),
                        int(row["proto"]),
                        int(row["src_port"]),
                        int(row["dst_port"]),
                    )
                except ValueError as exc:
                    raise PcapInputError("invalid label or flow identity") from exc
                if label not in TRAINING_LABELS:
                    raise PcapInputError("UNKNOWN is not a training label")
                if key.proto not in (6, 17) or any(
                    not 0 <= port <= 65535 for port in (key.client_port, key.server_port)
                ):
                    raise PcapInputError("invalid label transport/port")
                if key in labels or key.reverse() in labels or row["flow_id"] in identifiers:
                    raise PcapInputError("duplicate or ambiguous bidirectional label")
                identifiers.add(row["flow_id"])
                if row["pcap_file"] == pcap_name:
                    labels[key] = row
    except (OSError, UnicodeError, csv.Error) as exc:
        raise PcapInputError(f"cannot read labels: {path}") from exc
    return labels


def _pcap_packets(path: Path) -> Iterator[Packet]:
    try:
        validate_capture(path)
        previous = Decimal(-1)
        with PcapReader(str(path)) as reader:
            for packet in reader:
                timestamp = Decimal(str(packet.time))
                if not timestamp.is_finite() or timestamp < previous:
                    raise PcapInputError("capture timestamps must be finite and nondecreasing")
                previous = timestamp
                yield packet
    except (OSError, struct.error) as exc:
        raise PcapInputError(f"cannot parse pcap: {path}") from exc


def _packet_key(packet: Packet) -> FlowKey | None:
    if not isinstance(packet, Ether) or packet.type != 0x0800 or not isinstance(packet.payload, IP):
        return None  # §7.15: no feature state writes on unsupported parser paths.
    ip = packet[IP]
    raw = bytes(ip)
    header_bytes, total_bytes = int(ip.ihl or 0) * 4, int(ip.len or 0)
    if (
        ip.version != 4
        or ip.frag != 0
        or ip.flags.MF
        or not 20 <= header_bytes <= 60
        or not header_bytes <= total_bytes <= len(raw)
    ):
        return None  # §7.15: fragments and invalid declared lengths stay fail-open.
    transport = ip.payload
    transport_bytes = total_bytes - header_bytes
    if ip.proto == 6 and isinstance(transport, TCP):
        if not 20 <= int(transport.dataofs or 0) * 4 <= transport_bytes:
            return None
    elif ip.proto == 17 and isinstance(transport, UDP):
        if not 8 <= int(transport.len or 0) == transport_bytes:
            return None
    else:
        return None
    return FlowKey(ip.src, ip.dst, int(ip.proto), int(transport.sport), int(transport.dport))


def _observation(packet: Packet, upstream: bool) -> PacketObservation:
    ip = packet[IP]
    offset = int(ip.ihl) * 4
    offset += int(ip.payload.dataofs) * 4 if isinstance(ip.payload, TCP) else 8
    # §7.15: Ethernet padding is not application payload and cannot consume the TLS first-payload
    # opportunity. Full wire-frame bytes still contribute to the size features.
    payload = bytes(ip)[offset : int(ip.len)]
    return PacketObservation(
        int(Decimal(str(packet.time)) * 1_000_000),
        len(bytes(packet)),
        upstream,
        payload,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Extract measured pcap checkpoints to a feature CSV without replacing an existing file."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pcap", type=Path)
    parser.add_argument("labels", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        frame = extract_features(args.pcap, args.labels)
        with args.output.open("x", encoding="utf-8", newline="") as handle:
            frame.to_csv(handle, index=False)
    except (OSError, PcapInputError, ValueError) as exc:
        LOGGER.error("feature extraction failed: %s", exc)
        return 1
    LOGGER.info("extracted %d measured checkpoint rows to %s", len(frame), args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
