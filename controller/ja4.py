"""JA4 TLS client fingerprints from one complete, unfragmented ClientHello record.

Implements FoxIO's JA4 TCP/TLS field normalization (not JA4S/JA4H/QUIC). GREASE
values are excluded. No TCP reassembly or identification of a browser/agent is attempted.
Specification: https://github.com/FoxIO-LLC/ja4/blob/main/technical_details/JA4.md
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


class ClientHelloError(ValueError):
    """Raised for unsupported, truncated or malformed TLS ClientHello bytes."""


@dataclass
class _Reader:
    data: bytes
    position: int = 0

    def take(self, size: int) -> bytes:
        end = self.position + size
        if size < 0 or end > len(self.data):
            raise ClientHelloError("truncated TLS vector")
        result = self.data[self.position : end]
        self.position = end
        return result

    def integer(self, size: int) -> int:
        return int.from_bytes(self.take(size), "big")

    def vector(self, size: int) -> bytes:
        return self.take(self.integer(size))

    def finish(self) -> None:
        if self.position != len(self.data):
            raise ClientHelloError("TLS vector has trailing bytes")


def ja4(client_hello: bytes) -> str:
    """Return the TCP JA4 fingerprint of a complete first TLS ClientHello record.

    Args:
        client_hello: TLS record bytes beginning with content type 22. Later TLS records
            in the same segment are ignored; a fragmented ClientHello is unsupported.

    Raises:
        ClientHelloError: If a complete and structurally valid ClientHello is unavailable.
    """
    version, ciphers, extensions = _parse(client_hello)
    versions = (version,)
    if 43 in extensions:
        reader = _Reader(extensions[43])
        versions = _uint16s(reader.vector(1))
        reader.finish()
    valid_versions = [value for value in versions if not _grease(value)]
    version_code = {0x0300: "s3", 0x0301: "10", 0x0302: "11", 0x0303: "12", 0x0304: "13"}
    tls_version = version_code.get(max(valid_versions, default=0), "00")
    cipher_ids = [value for value in ciphers if not _grease(value)]
    ext_ids = [value for value in extensions if not _grease(value)]
    signatures: tuple[int, ...] = ()
    if 13 in extensions:
        reader = _Reader(extensions[13])
        signatures = _uint16s(reader.vector(2))
        reader.finish()
        signatures = tuple(value for value in signatures if not _grease(value))
    alpn = _alpn(extensions.get(16))
    sni = extensions.get(0)
    domain = "d" if sni is not None and _host_name(sni) is not None else "i"
    prefix = f"t{tls_version}{domain}{min(len(cipher_ids), 99):02}{min(len(ext_ids), 99):02}{alpn}"
    cipher_text = ",".join(f"{value:04x}" for value in sorted(cipher_ids))
    ext_text = ",".join(f"{value:04x}" for value in sorted(ext_ids) if value not in (0, 16))
    if signatures:
        ext_text += "_" + ",".join(f"{value:04x}" for value in signatures)
    return f"{prefix}_{_digest(cipher_text)}_{_digest(ext_text)}"


def _parse(payload: bytes) -> tuple[int, tuple[int, ...], dict[int, bytes]]:
    record = _Reader(payload)
    if record.integer(1) != 22 or record.integer(2) >> 8 != 3:
        raise ClientHelloError("not a TLS handshake record")
    handshake = _Reader(record.vector(2))
    if handshake.integer(1) != 1:
        raise ClientHelloError("not a ClientHello")
    body = _Reader(handshake.vector(3))
    version = body.integer(2)
    body.take(32)
    if len(body.vector(1)) > 32:
        raise ClientHelloError("TLS session identifier exceeds 32 bytes")
    ciphers = _uint16s(body.vector(2))
    if not ciphers or not body.vector(1):
        raise ClientHelloError("empty TLS ciphers or compression methods")
    extensions: dict[int, bytes] = {}
    if body.position < len(body.data):
        ext_reader = _Reader(body.vector(2))
        while ext_reader.position < len(ext_reader.data):
            ext_type = ext_reader.integer(2)
            if ext_type in extensions:
                raise ClientHelloError("duplicate TLS extension")
            extensions[ext_type] = ext_reader.vector(2)
    body.finish()
    return version, ciphers, extensions


def server_name(client_hello: bytes) -> str | None:
    """Return the SNI host name of a complete first TLS ClientHello record, if it carries one.

    Raises:
        ClientHelloError: If a complete and structurally valid ClientHello is unavailable.
    """
    _, _, extensions = _parse(client_hello)
    sni = extensions.get(0)
    return None if sni is None else _host_name(sni)


def _host_name(body: bytes) -> str | None:
    reader = _Reader(body)
    names = _Reader(reader.vector(2))
    reader.finish()
    found: dict[int, bytes] = {}
    while names.position < len(names.data):
        name_type = names.integer(1)
        name = names.vector(2)
        if not name or name_type in found:
            raise ClientHelloError("empty or duplicate SNI name")
        found[name_type] = name
    if not found:
        raise ClientHelloError("empty SNI name list")
    if 0 not in found:
        return None
    try:
        return found[0].decode("ascii").lower()
    except UnicodeDecodeError as exc:
        raise ClientHelloError("SNI host name is not ASCII") from exc


def _uint16s(data: bytes) -> tuple[int, ...]:
    if not data or len(data) % 2:
        raise ClientHelloError("TLS uint16 vector must be nonempty and even-sized")
    return tuple(int.from_bytes(data[index : index + 2], "big") for index in range(0, len(data), 2))


def _grease(value: int) -> bool:
    return value & 0x0F0F == 0x0A0A and value >> 8 == value & 0xFF


def _alpn(body: bytes | None) -> str:
    if body is None:
        return "00"
    reader = _Reader(body)
    protocols = _Reader(reader.vector(2))
    reader.finish()
    names: list[bytes] = []
    while protocols.position < len(protocols.data):
        names.append(protocols.vector(1))
    if not names or not names[0]:
        return "00"
    first = names[0]
    edges = bytes([first[0], first[-1]])
    if all(48 <= value <= 57 or 65 <= value <= 90 or 97 <= value <= 122 for value in edges):
        return edges.decode("ascii")
    encoded = first.hex()
    return encoded[0] + encoded[-1]


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()[:12] if value else "000000000000"
