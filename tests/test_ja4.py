"""JA4 vectors assembled from the documented TLS wire fields, without reassembly."""

from __future__ import annotations

import hashlib

import pytest

from controller.ja4 import ClientHelloError, ja4


def client_hello(
    ciphers: tuple[int, ...] = (0x1301, 0x1302, 0xC02F),
    extensions: tuple[tuple[int, bytes], ...] | None = None,
) -> bytes:
    """Build one complete TLS record for field-level fingerprint tests."""
    if extensions is None:
        extensions = (
            (0, b"\x00\x0c\x00\x00\x09localhost"),
            (16, b"\x00\x03\x02h2"),
            (43, b"\x04\x03\x04\x03\x03"),
            (13, b"\x00\x04\x04\x03\x08\x04"),
        )
    ext = b"".join(k.to_bytes(2, "big") + len(v).to_bytes(2, "big") + v for k, v in extensions)
    cipher_bytes = b"".join(c.to_bytes(2, "big") for c in ciphers)
    body = b"\x03\x03" + bytes(32) + b"\x00" + len(cipher_bytes).to_bytes(2, "big")
    body += cipher_bytes + b"\x01\x00" + len(ext).to_bytes(2, "big") + ext
    handshake = b"\x01" + len(body).to_bytes(3, "big") + body
    return b"\x16\x03\x01" + len(handshake).to_bytes(2, "big") + handshake


def test_ja4_vector_includes_supported_version_and_signature_order() -> None:
    # JA4 a = TCP, TLS1.3, domain SNI, 3 ciphers, 4 extensions, h2.
    cipher_hash = hashlib.sha256(b"1301,1302,c02f").hexdigest()[:12]
    extension_hash = hashlib.sha256(b"000d,002b_0403,0804").hexdigest()[:12]
    assert ja4(client_hello()) == f"t13d0304h2_{cipher_hash}_{extension_hash}"


def test_chrome_cipher_component_matches_published_ja4_example() -> None:
    ciphers = (
        0x002F,
        0x0035,
        0x009C,
        0x009D,
        0x1301,
        0x1302,
        0x1303,
        0xC013,
        0xC014,
        0xC02B,
        0xC02C,
        0xC02F,
        0xC030,
        0xCCA8,
        0xCCA9,
    )
    assert ja4(client_hello(ciphers)).split("_")[1] == "8daaf6152771"


def test_grease_is_ignored_and_cipher_extension_permutations_are_stable() -> None:
    normal = client_hello((0x1302, 0x1301), ((43, b"\x02\x03\x04"), (0x17, b"")))
    grease = client_hello(
        (0x2A2A, 0x1301, 0x1302), ((0x17, b""), (0x0A0A, b""), (43, b"\x04\xfa\xfa\x03\x04"))
    )
    assert ja4(normal) == ja4(grease)
    assert ja4(normal).startswith("t13i020200_")


def test_signature_order_changes_fingerprint_and_alpn_binary_encoding() -> None:
    left = client_hello(extensions=((13, b"\x00\x04\x04\x03\x08\x04"),))
    right = client_hello(extensions=((13, b"\x00\x04\x08\x04\x04\x03"),))
    assert ja4(left) != ja4(right)
    assert ja4(client_hello(extensions=((16, b"\x00\x03\x02\xab\xcd"),))).startswith("t12i0301ad_")


@pytest.mark.parametrize("payload", [b"", b"GET / HTTP/1.1", b"\x16\x03", client_hello()[:-1]])
def test_incomplete_or_non_client_hello_rejected(payload: bytes) -> None:
    with pytest.raises(ClientHelloError):
        ja4(payload)


def test_nested_tls_lengths_and_duplicate_extensions_rejected() -> None:
    with pytest.raises(ClientHelloError):
        ja4(client_hello(extensions=((13, b"\x00\x06\x04\x03"),)))
    with pytest.raises(ClientHelloError):
        ja4(client_hello(extensions=((43, b"\x02\x03\x04"), (43, b"\x02\x03\x04"))))


def test_empty_extension_list_and_tls12_legacy_version() -> None:
    value = ja4(client_hello(extensions=()))
    assert value.startswith("t12i030000_")
    assert value.endswith("_000000000000")


@pytest.mark.parametrize("body", [b"", b"\x00\x04\x02h2"])
def test_malformed_alpn_does_not_fingerprint(body: bytes) -> None:
    with pytest.raises(ClientHelloError):
        ja4(client_hello(extensions=((16, body),)))


@pytest.mark.parametrize("body", [b"", b"\x00\x08\x00\x00\x09localhost", b"\x00\x03\x00\x00\x00"])
def test_malformed_sni_cannot_produce_domain_evidence(body: bytes) -> None:
    with pytest.raises(ClientHelloError):
        ja4(client_hello(extensions=((0, body),)))


@pytest.mark.parametrize("body", [b"\x00\x00", b"\x00\x01\x00"])
def test_empty_alpn_values_normalize_to_zero_per_ja4_specification(body: bytes) -> None:
    assert ja4(client_hello(extensions=((16, body),))).startswith("t12i030100_")


def test_complete_published_foxio_vector() -> None:
    # FoxIO-LLC/ja4 technical_details/JA4.md, Example section, fetched 2026-10-28.
    ciphers = (
        0x1301,
        0x1302,
        0x1303,
        0xC02B,
        0xC02F,
        0xC02C,
        0xC030,
        0xCCA9,
        0xCCA8,
        0xC013,
        0xC014,
        0x009C,
        0x009D,
        0x002F,
        0x0035,
    )
    extension_ids = (
        0x001B,
        0,
        0x0033,
        16,
        0x4469,
        0x0017,
        0x002D,
        13,
        0x0005,
        0x0023,
        0x0012,
        43,
        0xFF01,
        0x000B,
        0x000A,
        0x0015,
    )
    signatures = bytes.fromhex("0403 0804 0401 0503 0805 0501 0806 0601")
    values = {
        0: b"\x00\x0c\x00\x00\x09localhost",
        16: b"\x00\x03\x02h2",
        43: b"\x04\x03\x04\x03\x03",
        13: len(signatures).to_bytes(2, "big") + signatures,
    }
    extensions = tuple((value, values.get(value, b"")) for value in extension_ids)
    assert ja4(client_hello(ciphers, extensions)) == "t13d1516h2_8daaf6152771_e5627efa2ab1"
