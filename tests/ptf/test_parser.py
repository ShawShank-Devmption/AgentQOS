"""P2.1 parser forwarding checks on BMv2 (design section 7.15)."""

import re
import subprocess

import ptf
from ptf.base_tests import BaseTest
from ptf.testutils import send_packet, verify_packet
from scapy.all import IP, TCP, UDP, Ether, Raw

DESTINATION_MAC = "00:00:00:00:00:02"
SOURCE_MAC = "00:00:00:00:00:01"
TLS_CLIENT_HELLO_BODY = b"\x03\x03" + bytes(32) + b"\x00\x00\x02\x13\x01\x01\x00\x00\x00"
TLS_HANDSHAKE = b"\x01" + len(TLS_CLIENT_HELLO_BODY).to_bytes(3, "big") + TLS_CLIENT_HELLO_BODY
TLS_RECORD = b"\x16\x03\x03" + len(TLS_HANDSHAKE).to_bytes(2, "big") + TLS_HANDSHAKE
TLS_SESSION_BODY = b"\x03\x03" + bytes(32) + b"\x04ABCD\x00\x02\x13\x01\x01\x00\x00\x00"
TLS_SESSION_HANDSHAKE = b"\x01" + len(TLS_SESSION_BODY).to_bytes(3, "big") + TLS_SESSION_BODY
TLS_SESSION_RECORD = (
    b"\x16\x03\x03" + len(TLS_SESSION_HANDSHAKE).to_bytes(2, "big") + TLS_SESSION_HANDSHAKE
)


def _tls_with_extensions(extensions: bytes) -> bytes:
    body = TLS_CLIENT_HELLO_BODY[:-2] + len(extensions).to_bytes(2, "big") + extensions
    handshake = b"\x01" + len(body).to_bytes(3, "big") + body
    return b"\x16\x03\x03" + len(handshake).to_bytes(2, "big") + handshake


def _alpn_extension(protocol: bytes) -> bytes:
    protocol_list = bytes((len(protocol),)) + protocol
    data = len(protocol_list).to_bytes(2, "big") + protocol_list
    return b"\x00\x10" + len(data).to_bytes(2, "big") + data


def _sni_extension(hostname: bytes) -> bytes:
    name = b"\x00" + len(hostname).to_bytes(2, "big") + hostname
    data = len(name).to_bytes(2, "big") + name
    return b"\x00\x00" + len(data).to_bytes(2, "big") + data


def _read_parser_probe(name: str = "reg_parser_probe") -> int:
    result = subprocess.run(
        ["simple_switch_CLI", "--thrift-port", "9090"],
        input=f"register_read {name} 0\n",
        capture_output=True,
        check=True,
        text=True,
    )
    match = re.search(rf"{re.escape(name)}\[0\]\s*=\s*(\d+)", result.stdout)
    if match is None:
        raise AssertionError(f"missing parser probe value: {result.stdout}")
    return int(match.group(1))


class ParserForwardingTest(BaseTest):
    """Verify unsupported and malformed traffic keeps the L2 forwarding path."""

    def setUp(self) -> None:
        super().setUp()
        self.dataplane = ptf.dataplane_instance
        self.dataplane.flush()

    def runTest(self) -> None:
        packets = (
            Ether(src=SOURCE_MAC, dst=DESTINATION_MAC, type=0x88B5) / Raw(b"non-ipv4"),
            Ether(src=SOURCE_MAC, dst=DESTINATION_MAC, type=0x0800) / Raw(b"short-ip"),
            Ether(src=SOURCE_MAC, dst=DESTINATION_MAC)
            / IP(src="10.0.0.1", dst="10.0.0.2")
            / UDP(sport=1111, dport=2222)
            / Raw(b"udp"),
            Ether(src=SOURCE_MAC, dst=DESTINATION_MAC)
            / IP(src="10.0.0.1", dst="10.0.0.2", len=100)
            / UDP(sport=1111, dport=2222),
            Ether(src=SOURCE_MAC, dst=DESTINATION_MAC)
            / IP(src="10.0.0.1", dst="10.0.0.2", proto=1)
            / Raw(b"icmp-like"),
            Ether(src=SOURCE_MAC, dst=DESTINATION_MAC)
            / IP(src="10.0.0.1", dst="10.0.0.2", options=b"\x01\x01\x01\x01")
            / TCP(sport=1234, dport=443),
            Ether(src=SOURCE_MAC, dst=DESTINATION_MAC)
            / IP(src="10.0.0.1", dst="10.0.0.2", frag=1)
            / Raw(b"later-fragment"),
            Ether(src=SOURCE_MAC, dst=DESTINATION_MAC)
            / IP(src="10.0.0.1", dst="10.0.0.2", proto=17)
            / Raw(b"short-l4"),
            Ether(src=SOURCE_MAC, dst=DESTINATION_MAC)
            / IP(src="10.0.0.1", dst="10.0.0.2")
            / TCP(sport=1234, dport=443)
            / Raw(b"\x16\x03\x03\x00\x02\x01"),
            Ether(src=SOURCE_MAC, dst=DESTINATION_MAC)
            / IP(src="10.0.0.1", dst="10.0.0.2")
            / TCP(sport=1234, dport=443)
            / Raw(TLS_RECORD),
            Ether(src=SOURCE_MAC, dst=DESTINATION_MAC)
            / IP(src="10.0.0.1", dst="10.0.0.2")
            / TCP(sport=1234, dport=443, options=[("NOP", None)])
            / Raw(TLS_RECORD),
            Ether(src=SOURCE_MAC, dst=DESTINATION_MAC)
            / IP(src="10.0.0.1", dst="10.0.0.2")
            / TCP(sport=1234, dport=443)
            / Raw(TLS_SESSION_RECORD),
            Ether(src=SOURCE_MAC, dst=DESTINATION_MAC)
            / IP(src="10.0.0.1", dst="10.0.0.2")
            / TCP(sport=1234, dport=443)
            / Raw(_tls_with_extensions(b"\x12\x34\x00\x02\xab\xcd")),
            Ether(src=SOURCE_MAC, dst=DESTINATION_MAC)
            / IP(src="10.0.0.1", dst="10.0.0.2")
            / TCP(sport=1234, dport=443)
            / Raw(_tls_with_extensions(b"\xaa\xaa\x00\x00" * 16)),
            Ether(src=SOURCE_MAC, dst=DESTINATION_MAC)
            / IP(src="10.0.0.1", dst="10.0.0.2")
            / TCP(sport=1234, dport=443)
            / Raw(_tls_with_extensions(b"\xaa\xaa\x00\x00" * 17)),
            Ether(src=SOURCE_MAC, dst=DESTINATION_MAC)
            / IP(src="10.0.0.1", dst="10.0.0.2")
            / TCP(sport=1234, dport=443)
            / Raw(_tls_with_extensions(b"\x12\x34\x00\x0a")),
            Ether(src=SOURCE_MAC, dst=DESTINATION_MAC)
            / IP(src="10.0.0.1", dst="10.0.0.2")
            / TCP(sport=1234, dport=443)
            / Raw(_tls_with_extensions(b"\x12\x34\x02\x01" + bytes(513))),
            Ether(src=SOURCE_MAC, dst=DESTINATION_MAC)
            / IP(src="10.0.0.1", dst="10.0.0.2")
            / TCP(sport=1234, dport=443)
            / Raw(b"\x16\x03\x03\x00\xff" + TLS_HANDSHAKE),
            Ether(src=SOURCE_MAC, dst=DESTINATION_MAC)
            / IP(src="10.0.0.1", dst="10.0.0.2")
            / TCP(sport=1234, dport=443)
            / Raw(b"\x16\x03\x03\x00\x2f\x01\x00\x00\xff" + TLS_CLIENT_HELLO_BODY),
        )
        for packet in packets:
            with self.subTest(packet=packet.summary()):
                wire_packet = bytes(packet)
                send_packet(self, 0, wire_packet)
                verify_packet(self, wire_packet, 1)


class ParserMetadataTest(BaseTest):
    """Check bounded parser metadata through the test-only BMv2 register."""

    def setUp(self) -> None:
        super().setUp()
        self.dataplane = ptf.dataplane_instance
        self.dataplane.flush()

    def runTest(self) -> None:
        udp_packet = Ether(src=SOURCE_MAC, dst=DESTINATION_MAC) / IP() / UDP()
        truncated_ipv4 = Ether(src=SOURCE_MAC, dst=DESTINATION_MAC) / IP(len=100) / UDP()
        fragment = Ether(src=SOURCE_MAC, dst=DESTINATION_MAC) / IP(frag=1) / Raw(b"later")
        ipv4_options = (
            Ether(src=SOURCE_MAC, dst=DESTINATION_MAC) / IP(options=b"\x01\x01\x01\x01") / UDP()
        )
        base = Ether(src=SOURCE_MAC, dst=DESTINATION_MAC) / IP() / TCP(dport=443)
        options = (
            Ether(src=SOURCE_MAC, dst=DESTINATION_MAC)
            / IP()
            / TCP(dport=443, options=[("NOP", None)])
        )
        cases = (
            (Ether(src=SOURCE_MAC, dst=DESTINATION_MAC, type=0x88B5) / Raw(b"other"), 0),
            (udp_packet, 1),
            (truncated_ipv4, 0),
            (fragment, 0),
            (ipv4_options, 0),
            (base / Raw(b"\x17" + TLS_RECORD[1:]), 1),
            (base / Raw(b"\x16\x03\x03\x00\xff" + TLS_HANDSHAKE), 0),
            (base / Raw(TLS_RECORD), 2051),
            (options / Raw(TLS_RECORD), 2051),
            (base / Raw(_tls_with_extensions(b"\x12\x34\x00\x02\xab\xcd")), 2059),
            (base / Raw(_tls_with_extensions(b"\xaa\xaa\x00\x00" * 16)), 2179),
            (base / Raw(_tls_with_extensions(b"\xaa\xaa\x00\x00" * 17)), 2183),
            (base / Raw(_tls_with_extensions(b"\x12\x34\x00\x0a")), 2048),
            (base / Raw(_tls_with_extensions(b"\x00\x10\x00\x05\x00\x05\x02h2")), 2048),
            (
                base / Raw(_tls_with_extensions(b"\x00\x00\x00\x06\x00\x04\x00\x00\x10a")),
                2048,
            ),
            (base / Raw(_tls_with_extensions(b"\x12\x34\x02\x01" + bytes(513))), 2055),
        )
        for packet, expected in cases:
            with self.subTest(packet=packet.summary()):
                wire_packet = bytes(packet)
                send_packet(self, 0, wire_packet)
                verify_packet(self, wire_packet, 1)
                self.assertEqual(_read_parser_probe(), expected)

        alpn_sni_cases = (
            (_alpn_extension(b"h2"), 2059, (1 << 18) | (1 << 20)),
            (_alpn_extension(b"http/1.1"), 2059, (1 << 18) | (1 << 19)),
            (_alpn_extension(b"foo"), 2059, (1 << 18) | (1 << 21)),
            (_sni_extension(b"a" * 16), 2059, (1 << 16) | 16),
            (_sni_extension(b"a" * 33), 2059, (2 << 16) | 33),
            (_sni_extension(b"a" * 129), 2059, (3 << 16) | 129),
        )
        for extension, expected_main, expected_aux in alpn_sni_cases:
            packet = base / Raw(_tls_with_extensions(extension))
            with self.subTest(extension=extension[:4]):
                wire_packet = bytes(packet)
                send_packet(self, 0, wire_packet)
                verify_packet(self, wire_packet, 1)
                self.assertEqual(_read_parser_probe(), expected_main)
                self.assertEqual(
                    _read_parser_probe("reg_parser_probe_aux"), expected_aux | (1 << 22)
                )

        for packet, expected_payload_flag in ((base, 0), (base / Raw(TLS_RECORD), 1 << 22)):
            with self.subTest(payload=bool(expected_payload_flag)):
                wire_packet = bytes(packet)
                send_packet(self, 0, wire_packet)
                verify_packet(self, wire_packet, 1)
                self.assertEqual(_read_parser_probe("reg_parser_probe_aux"), expected_payload_flag)
