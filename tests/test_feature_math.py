"""Hand-computed fixed-point cases for the P4 feature arithmetic mirror (P2.4, §7.3, §7.9)."""

import pytest

from common.contracts import CM_SKETCH_COLUMNS, CM_SKETCH_ROWS, FLOW_SLOTS, AlpnClass
from common.feature_math import (
    ewma_step,
    first8_size_bucket,
    floor_log2,
    flow_age_ms,
    flow_key_bytes,
    flow_slot,
    flow_tag,
    iat_update,
    mean_pkt_size_up,
    pack_proto_meta,
    parse_client_hello_meta,
    saturate,
    sketch_columns,
    timestamp_delta_us,
    unpack_proto_meta,
    updown_ratio_x100,
)


def _client_hello(extensions: list[tuple[int, bytes]]) -> bytes:
    ext_blob = b"".join(
        t.to_bytes(2, "big") + len(body).to_bytes(2, "big") + body for t, body in extensions
    )
    body = (
        b"\x03\x03"
        + bytes(32)
        + b"\x00"
        + b"\x00\x02\x13\x01"
        + b"\x01\x00"
        + len(ext_blob).to_bytes(2, "big")
        + ext_blob
    )
    handshake = b"\x01" + len(body).to_bytes(3, "big") + body
    return b"\x16\x03\x01" + len(handshake).to_bytes(2, "big") + handshake


def _alpn(*protocols: bytes) -> bytes:
    names = b"".join(len(p).to_bytes(1, "big") + p for p in protocols)
    return len(names).to_bytes(2, "big") + names


def test_saturate_clamps_and_rejects_negative() -> None:
    assert saturate(70_000, 16) == 65_535
    assert saturate(5, 16) == 5
    with pytest.raises(ValueError):
        saturate(-1, 16)


def test_floor_log2() -> None:
    assert [floor_log2(v) for v in (1, 6, 8, 3_900)] == [0, 2, 3, 11]
    with pytest.raises(ValueError):
        floor_log2(0)


def test_timestamp_delta_wraps_48_bits_and_clamps_32() -> None:
    assert timestamp_delta_us(2_000, 500) == 1_500
    assert timestamp_delta_us(5, (1 << 48) - 5) == 10  # §7.3 wraparound
    assert timestamp_delta_us(1 << 40, 0) == (1 << 32) - 1


def test_ewma_step_truncates_toward_zero_like_unsigned_p4() -> None:
    assert ewma_step(0, 80) == 10
    assert ewma_step(100, 20) == 90
    assert ewma_step(10, 3) == 10  # Python floor shift would give 9


def test_iat_update_uses_pre_update_mean_for_deviation() -> None:
    assert iat_update(100_000, 0, 20_000) == (90_000, 10_000)
    assert iat_update(90_000, 10_000, 350_000) == (122_500, 41_250)


def test_mean_pkt_size_up() -> None:
    assert mean_pkt_size_up(1_200, 6) == 300
    assert mean_pkt_size_up(0, 0) == 0
    assert mean_pkt_size_up((1 << 32) - 1, 1) == 65_535


def test_updown_ratio_x100() -> None:
    assert updown_ratio_x100(1_200, 3_600) == 58
    assert updown_ratio_x100(3_200, 9_600) == 39
    assert updown_ratio_x100(5, 0) == 65_535  # §7.14 asymmetric visibility caps


def test_first8_size_bucket_first_packet_is_msb() -> None:
    assert first8_size_bucket([400, 1_200, 400, 1_200, 400, 1_200]) == 0b1111_1100
    assert first8_size_bucket([74, 1_500, 66, 900, 66, 1_500]) == 0b0101_0100
    assert first8_size_bucket([128]) == 0b1000_0000
    assert first8_size_bucket([]) == 0
    with pytest.raises(ValueError):
        first8_size_bucket([0] * 9)


def test_flow_age_is_microseconds_shifted_by_ten() -> None:
    assert flow_age_ms(10_000, 0) == 9
    assert flow_age_ms(625_000, 0) == 610


def test_proto_meta_round_trip() -> None:
    assert pack_proto_meta(3, AlpnClass.H2) == 0x0302
    assert unpack_proto_meta(0x0302) == (3, AlpnClass.H2)


@pytest.mark.parametrize(
    ("alpn_body", "expected"),
    [
        (_alpn(b"h2", b"http/1.1"), AlpnClass.H2),
        (_alpn(b"http/1.1"), AlpnClass.H1),
        (_alpn(b"h3"), AlpnClass.OTHER),
    ],
)
def test_client_hello_alpn_class(alpn_body: bytes, expected: AlpnClass) -> None:
    payload = _client_hello([(0x0000, b"\x00"), (0x0010, alpn_body), (0x002B, b"\x02\x03\x04")])
    assert parse_client_hello_meta(payload) == (3, expected)


def test_client_hello_without_alpn() -> None:
    assert parse_client_hello_meta(_client_hello([(0x0000, b"")])) == (1, AlpnClass.NONE)


def test_client_hello_counts_at_most_sixteen_extensions() -> None:
    payload = _client_hello([(0x0100 + i, b"") for i in range(20)])
    assert parse_client_hello_meta(payload) == (16, AlpnClass.NONE)


def test_client_hello_split_across_segments_counts_complete_extensions_only() -> None:
    payload = _client_hello([(0x0000, b"abcd"), (0x0010, _alpn(b"h2"))])
    assert parse_client_hello_meta(payload[:-2]) == (1, AlpnClass.NONE)


@pytest.mark.parametrize(
    "payload",
    [b"", b"GET / HTTP/1.1\r\n", b"\x16\x03\x01\x00\x05\x02", _client_hello([])[:20]],
)
def test_non_client_hello_payloads_fail_open(payload: bytes) -> None:
    assert parse_client_hello_meta(payload) == (0, AlpnClass.NONE)  # §7.15


def test_flow_key_layout_and_hash_ranges() -> None:
    key = flow_key_bytes(0x0A00_0001, 0x0A00_0002, 6, 40_000, 443)
    assert key == bytes.fromhex("0a000001 0a000002 06 9c40 01bb")
    assert 0 <= flow_slot(key) < FLOW_SLOTS
    assert 0 <= flow_tag(key) < 1 << 32
    assert flow_slot(key) == flow_slot(bytes(key))  # deterministic
    columns = sketch_columns(0x0A00_0001)
    assert len(columns) == CM_SKETCH_ROWS
    assert all(0 <= c < CM_SKETCH_COLUMNS for c in columns)
