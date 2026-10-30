"""Host-inspectable wire cases shared with the C2 parser PTF suite."""

from dataclasses import dataclass

from scapy.layers.inet import IP, TCP, UDP
from scapy.layers.l2 import Ether
from scapy.packet import Raw


@dataclass(frozen=True)
class ParserCase:
    """An exact frame and the expected parser eligibility bit."""

    name: str
    frame: bytes
    feature_valid: int


def parser_cases() -> tuple[ParserCase, ...]:
    eth = Ether(src="00:00:00:00:00:01", dst="00:00:00:00:00:02")
    tcp = eth / IP(src="10.0.0.1", dst="10.0.0.2") / TCP(sport=40000, dport=443)
    udp = eth / IP(src="10.0.0.1", dst="10.0.0.2") / UDP(sport=40000, dport=53)
    frames = [
        ParserCase("tcp", bytes(tcp), 1),
        ParserCase("plain_tcp_large", bytes(tcp / Raw(b"x" * 80)), 1),
        ParserCase("short_tls_prefix", bytes(tcp / Raw(b"\x16\x03")), 0),
        ParserCase("invalid_tls_record", bytes(tcp / Raw(b"\x16\x03" + b"\x00" * 42)), 0),
        ParserCase(
            "short_client_hello", bytes(tcp / Raw(b"\x16\x03\x03\x00\x04\x01\x00\x00\x00")), 0
        ),
        ParserCase(
            "non_client_hello", bytes(tcp / Raw(b"\x16\x03\x03\x00\x04\x02\x00\x00\x00")), 1
        ),
        ParserCase("udp", bytes(udp), 1),
        ParserCase("ethernet_padding", bytes(tcp) + b"\x00" * 20, 1),
        ParserCase("ipv4_options", bytes(eth / IP(options=b"\x01" * 40) / TCP()), 1),
        ParserCase("tcp_options", bytes(eth / IP() / TCP(options=[("NOP", None)] * 40)), 1),
        ParserCase(
            "both_options",
            bytes(eth / IP(options=b"\x01" * 4) / TCP(options=[("MSS", 1460)])),
            1,
        ),
        ParserCase("non_ipv4", bytes(Ether(type=0x86DD) / Raw(b"\x60" * 64)), 0),
        ParserCase("fragment_offset", bytes(eth / IP(frag=1) / TCP()), 0),
        ParserCase("first_fragment", bytes(eth / IP(flags="MF") / TCP()), 0),
        ParserCase("icmp", bytes(eth / IP(proto=1) / Raw(b"\x00" * 8)), 0),
        ParserCase("ip_version", bytes(eth / IP(version=6) / TCP()), 0),
        ParserCase("short_ihl", bytes(eth / IP(ihl=4) / TCP()), 0),
        ParserCase("short_total_len", bytes(eth / IP(len=19) / TCP()), 0),
        ParserCase("long_total_len", bytes(eth / IP(len=1000) / TCP()), 0),
        ParserCase("short_tcp_offset", bytes(eth / IP() / TCP(dataofs=4)), 0),
        ParserCase("long_tcp_offset", bytes(eth / IP() / TCP(dataofs=15)), 0),
        ParserCase("missing_tcp", bytes(eth / IP(proto=6)), 0),
        ParserCase("partial_tcp", bytes(eth / IP(proto=6) / Raw(b"\x00" * 19)), 0),
        ParserCase("partial_udp", bytes(eth / IP(proto=17) / Raw(b"\x00" * 7)), 0),
        ParserCase("short_udp_length", bytes(eth / IP() / UDP(len=7)), 0),
        ParserCase("long_udp_length", bytes(eth / IP() / UDP(len=9)), 0),
        # §7.15: AF_PACKET requires Ethernet's full 14-byte header; IPv4 is truncated.
        ParserCase("ethernet_only_ipv4", bytes(tcp)[:14], 0),
        ParserCase("partial_ipv4", bytes(tcp)[:33], 0),
        ParserCase("partial_ipv4_options", bytes(eth / IP(ihl=15, len=60)), 0),
    ]
    return tuple(frames)
