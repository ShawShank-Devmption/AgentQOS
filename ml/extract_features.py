"""Per-flow feature extraction mirroring the P4 feature stage (design.md 5.2, 8.2; P2.4/P3.3)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from common.contracts import FEATURE_CHECKPOINTS, FIRST_SIZE_COUNT, AlpnClass
from common.feature_math import (
    first8_size_bucket,
    flow_age_ms,
    iat_update,
    mean_pkt_size_up,
    parse_client_hello_meta,
    saturate,
    timestamp_delta_us,
    updown_ratio_x100,
)


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
