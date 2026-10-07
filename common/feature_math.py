"""Python mirror of the P4 fixed-point feature arithmetic (design.md sections 4.3, 7.3, 7.9).

Each function reproduces what p4src/features.p4 computes, bit for bit, as defined in
docs/feature_arithmetic.md. Pure stdlib and Python 3.9 safe: imported by ml/ and controller/.
"""

from __future__ import annotations

import zlib
from collections.abc import Sequence

from common.contracts import (
    CM_SKETCH_COLUMNS,
    CM_SKETCH_ROWS,
    EWMA_SHIFT,
    FIRST_SIZE_COUNT,
    FIRST_SIZE_LARGE_BYTES,
    FLOW_AGE_SHIFT,
    FLOW_KEY_SLOT_SALT,
    FLOW_KEY_TAG_SALT,
    FLOW_SLOTS,
    PROTO_META_EXT_COUNT_SHIFT,
    TIMESTAMP_BITS,
    TLS_MAX_EXTENSIONS,
    UPDOWN_RATIO_SCALE,
    AlpnClass,
)

_ALPN_EXTENSION_TYPE = 0x0010
_TLS_RECORD_HEADER_BYTES = 5
_HANDSHAKE_HEADER_BYTES = 4
_CLIENT_HELLO_TYPE = 0x01
_VERSION_AND_RANDOM_BYTES = 2 + 32


def saturate(value: int, bits: int) -> int:
    """Clamp a non-negative value to an unsigned `bits`-wide field (section 7.3)."""
    if value < 0:
        raise ValueError(f"fixed-point values are unsigned, got {value}")
    return min(value, (1 << bits) - 1)


def floor_log2(value: int) -> int:
    """Return the highest set bit index: the P4 shift that stands in for `/ value`."""
    if value <= 0:
        raise ValueError(f"floor_log2 requires a positive value, got {value}")
    return value.bit_length() - 1


def timestamp_delta_us(now_us: int, earlier_us: int) -> int:
    """Return the 48-bit wrapping difference clamped to 32 bits (section 7.3)."""
    return saturate((now_us - earlier_us) % (1 << TIMESTAMP_BITS), 32)


def ewma_step(current: int, sample: int) -> int:
    """Apply `current += (sample - current) >> EWMA_SHIFT` on unsigned registers.

    P4 shifts the magnitude, then applies the sign, which truncates toward zero; Python's `>>`
    on a negative difference floors instead, hence the two branches.
    """
    if sample >= current:
        return current + ((sample - current) >> EWMA_SHIFT)
    return current - ((current - sample) >> EWMA_SHIFT)


def iat_update(iat_ewma: int, iat_var_ewma: int, delta_us: int) -> tuple[int, int]:
    """Update the IAT mean and mean-absolute-deviation EWMAs for one non-zero gap.

    The deviation is taken against the pre-update mean so P4 reads each register once.
    """
    deviation = abs(delta_us - iat_ewma)
    return ewma_step(iat_ewma, delta_us), ewma_step(iat_var_ewma, deviation)


def mean_pkt_size_up(bytes_up: int, pkt_count: int) -> int:
    """Feature 3: `bytes_up >> floor_log2(pkt_count)`, saturated to 16 bits.

    `pkt_count` counts both directions and the shift rounds the divisor down to a power of
    two, so the value over-reads the true mean by less than 2x.
    """
    if pkt_count == 0:
        return 0
    return saturate(bytes_up >> floor_log2(pkt_count), 16)


def updown_ratio_x100(bytes_up: int, bytes_down: int) -> int:
    """Feature 4: `(bytes_up * 100) >> floor_log2(bytes_down)`, saturated to 16 bits.

    P4 computes `* 100` as `(x << 6) + (x << 5) + (x << 2)`. No downstream bytes (including
    one-way visibility, section 7.14) reads as the cap.
    """
    if bytes_down == 0:
        return (1 << 16) - 1
    return saturate((bytes_up * UPDOWN_RATIO_SCALE) >> floor_log2(bytes_down), 16)


def first8_size_bucket(first_sizes: Sequence[int]) -> int:
    """Feature 5: bit (7 - i) is set when packet i is at least FIRST_SIZE_LARGE_BYTES.

    The first packet is the most significant bit, so tree splits compare early packets first.
    """
    if len(first_sizes) > FIRST_SIZE_COUNT:
        raise ValueError(f"at most {FIRST_SIZE_COUNT} first sizes, got {len(first_sizes)}")
    bucket = 0
    for index, size in enumerate(first_sizes):
        if size >= FIRST_SIZE_LARGE_BYTES:
            bucket |= 1 << (FIRST_SIZE_COUNT - 1 - index)
    return bucket


def flow_age_ms(now_us: int, first_us: int) -> int:
    """Feature 9: flow age in 1.024 ms units (`delta_us >> 10`)."""
    return timestamp_delta_us(now_us, first_us) >> FLOW_AGE_SHIFT


def pack_proto_meta(ext_count: int, alpn: AlpnClass) -> int:
    """Pack features 7-8 the way reg_proto_meta stores them."""
    return (ext_count << PROTO_META_EXT_COUNT_SHIFT) | int(alpn)


def unpack_proto_meta(value: int) -> tuple[int, AlpnClass]:
    """Unpack reg_proto_meta into (tls_ext_count, tls_alpn_class)."""
    return (value >> PROTO_META_EXT_COUNT_SHIFT) & 0xFF, AlpnClass(value & 0xFF)


def parse_client_hello_meta(payload: bytes) -> tuple[int, AlpnClass]:
    """Features 7-8 from a flow's first payload segment, mirroring the bounded P4 parse.

    Counts extensions whose header and body lie inside both this segment and the extensions
    block, stopping at TLS_MAX_EXTENSIONS. A payload that is not a ClientHello parseable up to
    its extensions block yields the fail-open value (0, NONE) (section 7.15).
    """
    fail_open = (0, AlpnClass.NONE)
    if (
        payload[:2] != b"\x16\x03"
        or len(payload) <= _TLS_RECORD_HEADER_BYTES
        or payload[_TLS_RECORD_HEADER_BYTES] != _CLIENT_HELLO_TYPE
    ):
        return fail_open
    position = _TLS_RECORD_HEADER_BYTES + _HANDSHAKE_HEADER_BYTES + _VERSION_AND_RANDOM_BYTES
    for length_bytes in (1, 2, 1):  # session id, cipher suites, compression methods
        position = _skip_vector(payload, position, length_bytes)
        if position < 0:
            return fail_open
    if position + 2 > len(payload):
        return fail_open
    declared = int.from_bytes(payload[position : position + 2], "big")
    block_end = min(len(payload), position + 2 + declared)
    position += 2
    count = 0
    alpn = AlpnClass.NONE
    while count < TLS_MAX_EXTENSIONS and position + 4 <= block_end:
        ext_type = int.from_bytes(payload[position : position + 2], "big")
        body_end = position + 4 + int.from_bytes(payload[position + 2 : position + 4], "big")
        if body_end > block_end:
            break
        if ext_type == _ALPN_EXTENSION_TYPE:
            alpn = _alpn_class(payload[position + 4 : body_end])
        count += 1
        position = body_end
    return count, alpn


def flow_key_bytes(
    client_ip: int, server_ip: int, proto: int, client_port: int, server_port: int
) -> bytes:
    """Canonical 104-bit flow key, client side first, as P4 orders it by ingress port."""
    return (
        client_ip.to_bytes(4, "big")
        + server_ip.to_bytes(4, "big")
        + proto.to_bytes(1, "big")
        + client_port.to_bytes(2, "big")
        + server_port.to_bytes(2, "big")
    )


def flow_slot(key: bytes) -> int:
    """Register index: BMv2 `hash(crc32, {key, 8w0}) % FLOW_SLOTS`."""
    return zlib.crc32(key + bytes([FLOW_KEY_SLOT_SALT])) % FLOW_SLOTS


def flow_tag(key: bytes) -> int:
    """Slot ownership tag stored in reg_flow_key: BMv2 `hash(crc32, {key, 8w1})` (7.1)."""
    return zlib.crc32(key + bytes([FLOW_KEY_TAG_SALT]))


def sketch_columns(src_ip: int) -> tuple[int, ...]:
    """CM-sketch column for each row: BMv2 `hash(crc32, {src_ip, 8w row}) % columns`."""
    source = src_ip.to_bytes(4, "big")
    return tuple(
        zlib.crc32(source + bytes([row])) % CM_SKETCH_COLUMNS for row in range(CM_SKETCH_ROWS)
    )


def _skip_vector(data: bytes, position: int, length_bytes: int) -> int:
    """Skip a TLS length-prefixed vector; -1 when it runs past the segment."""
    header_end = position + length_bytes
    if header_end > len(data):
        return -1
    end = header_end + int.from_bytes(data[position:header_end], "big")
    return end if end <= len(data) else -1


def _alpn_class(body: bytes) -> AlpnClass:
    """Classify the first protocol of an ALPN extension body (RFC 7301)."""
    if len(body) < 3:
        return AlpnClass.OTHER
    name = body[3 : 3 + body[2]]
    if name == b"http/1.1":
        return AlpnClass.H1
    if name == b"h2":
        return AlpnClass.H2
    return AlpnClass.OTHER
