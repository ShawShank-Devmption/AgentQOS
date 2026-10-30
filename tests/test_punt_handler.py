"""CPU clone parsing, canonical flow identity and bounded best-effort refinement."""

import struct

import pytest

from common.contracts import PUNT_REASON_FIRST_PACKET, TrafficClass
from common.feature_math import flow_key_bytes, flow_slot
from controller.punt_handler import BoundedPuntQueue, PuntHandler, PuntParseError, parse_punt
from tests.test_ja4 import client_hello


def punt_frame(payload: bytes = b"", reverse: bool = False, proto: int = 6) -> bytes:
    """Build a CPU-header plus Ethernet/IPv4/TCP or UDP packet."""
    src, dst, sport, dport = (0x0A000001, 0x0A000064, 40000, 443)
    key = flow_key_bytes(src, dst, proto, sport, dport)
    if reverse:
        src, dst, sport, dport = dst, src, dport, sport
    cpu = struct.pack("!HBBHH", flow_slot(key), 0, PUNT_REASON_FIRST_PACKET, 4 if reverse else 1, 0)
    eth = bytes(12) + b"\x08\x00"
    l4 = struct.pack("!HHIIHHHH", sport, dport, 0, 0, 0x5018, 8192, 0, 0)
    if proto == 17:
        l4 = struct.pack("!HHHH", sport, dport, 8 + len(payload), 0)
    ip = struct.pack(
        "!BBHHHBBHII", 0x45, 0, 20 + len(l4) + len(payload), 0, 0, 64, proto, 0, src, dst
    )
    return cpu + eth + ip + l4 + payload


def test_bidirectional_punts_share_canonical_identity() -> None:
    up = parse_punt(punt_frame(), {1, 2, 3}, {4})
    down = parse_punt(punt_frame(reverse=True), {1, 2, 3}, {4})
    assert up.flow == down.flow
    assert up.flow.key == flow_key_bytes(0x0A000001, 0x0A000064, 6, 40000, 443)
    assert up.header.flow_hash == flow_slot(up.flow.key)
    assert parse_punt(punt_frame(proto=17), {1}, {4}).flow.proto == 17


@pytest.mark.parametrize("offset,value", [(20, 0x86), (22, 0x65), (28, 1), (34, 1), (54, 0x10)])
def test_malformed_frame_is_fail_open_in_handler(offset: int, value: int) -> None:
    raw = bytearray(punt_frame())
    raw[offset] = value
    assert PuntHandler({1}, {4}).handle(bytes(raw)) is None


def test_truncation_bad_cpu_class_port_and_slot_rejected() -> None:
    valid = punt_frame()
    for end in (0, 7, 20, 35, len(valid) - 1):
        with pytest.raises(PuntParseError):
            parse_punt(valid[:end], {1}, {4})
    for offset, value in ((0, valid[0] ^ 1), (2, 255), (5, 99)):
        bad = bytearray(valid)
        bad[offset] = value
        with pytest.raises(PuntParseError):
            parse_punt(bytes(bad), {1}, {4})


def test_queue_drops_new_punts_without_blocking() -> None:
    queue = BoundedPuntQueue(1)
    assert queue.offer(b"first")
    assert not queue.offer(b"second")
    assert queue.dropped == 1
    assert queue.pop() == b"first"
    assert queue.pop() is None
    with pytest.raises(ValueError):
        BoundedPuntQueue(0)


def test_only_explicit_ja4_evidence_refines_and_bad_tls_keeps_flow() -> None:
    from controller.ja4 import ja4

    hello = client_hello()
    handler = PuntHandler({1}, {4}, agent_fingerprints=frozenset({ja4(hello)}))
    observation = handler.handle(punt_frame(hello))
    assert observation is not None
    assert observation.evidence_class == TrafficClass.AGENT_INTERACTIVE
    assert PuntHandler({1}, {4}).handle(punt_frame(hello)).evidence_class is None
    malformed = handler.handle(punt_frame(hello[:-1]))
    assert malformed is not None and malformed.evidence_class is None


def test_ethernet_padding_is_not_exposed_as_transport_payload() -> None:
    assert parse_punt(punt_frame(b"real") + b"padding", {1}, {4}).payload == b"real"


def test_ipv4_options_and_tcp_options_are_skipped_correctly() -> None:
    frame = bytearray(punt_frame(b"real"))
    frame[22] = 0x46
    frame[24:26] = (48).to_bytes(2, "big")
    frame[42:42] = bytes(4)
    assert parse_punt(bytes(frame), {1}, {4}).payload == b"real"
    frame = bytearray(punt_frame(b"real"))
    frame[24:26] = (48).to_bytes(2, "big")
    frame[54] = 0x60
    frame[62:62] = bytes(4)
    assert parse_punt(bytes(frame), {1}, {4}).payload == b"real"


def test_low_confidence_and_reserved_punts_do_not_apply_tls_evidence() -> None:
    from controller.ja4 import ja4

    hello = client_hello()
    handler = PuntHandler({1}, {4}, frozenset({ja4(hello)}))
    for reason in (2, 3, 255):
        frame = bytearray(punt_frame(hello))
        frame[3] = reason
        observation = handler.handle(bytes(frame))
        assert observation is not None and observation.evidence_class is None


def test_port_direction_configuration_is_validated() -> None:
    with pytest.raises(ValueError):
        PuntHandler({1}, {1})


@pytest.mark.parametrize("ports", [{-1}, {0}, {65536}, {True}])
def test_invalid_topology_port_values_fail_at_configuration_boundary(ports: set[int]) -> None:
    with pytest.raises(ValueError):
        PuntHandler(ports, {4})
