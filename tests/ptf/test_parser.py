"""P2.1 PTF forwarding tests for the isolated parser harness in docs/parser_completion.md."""

import logging
import re
import subprocess
from dataclasses import dataclass

import pytest
from scapy.layers.inet import IP, TCP, UDP
from scapy.layers.l2 import Ether
from scapy.packet import Raw

from common.contracts import TrafficClass

pytest.importorskip(
    "ptf.base_tests", reason="PTF runs against BMv2 in the Linux data-plane environment"
)
import ptf  # noqa: E402
from ptf import testutils  # noqa: E402
from ptf.base_tests import BaseTest  # noqa: E402

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ParserCase:
    """An exact frame and the expected parser eligibility bit."""

    name: str
    frame: bytes
    feature_valid: int


def _cases() -> tuple[ParserCase, ...]:
    eth = Ether(src="00:00:00:00:00:01", dst="00:00:00:00:00:02")
    tcp = eth / IP(src="10.0.0.1", dst="10.0.0.2") / TCP(sport=40000, dport=443)
    udp = eth / IP(src="10.0.0.1", dst="10.0.0.2") / UDP(sport=40000, dport=53)
    frames = [
        ParserCase("tcp", bytes(tcp), 1),
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
        ParserCase("short_ethernet", bytes(tcp)[:13], 0),
        ParserCase("partial_ipv4", bytes(tcp)[:33], 0),
        ParserCase("partial_ipv4_options", bytes(eth / IP(ihl=15, len=60)), 0),
    ]
    return tuple(frames)


class ParserForwardingTest(BaseTest):
    """Verify frame preservation and observed metadata for every base-parser branch."""

    def setUp(self) -> None:  # noqa: N802
        """Connect to the fixed-port parser harness; fail if observation is unavailable."""
        super().setUp()
        # PTF's BaseTest owns no dataplane; tests send through the runner's instance.
        self.dataplane = ptf.dataplane_instance
        self.dataplane.flush()
        params = testutils.test_params_get()
        self.ingress_port = int(params.get("ingress_port", 0))
        self.egress_port = int(params.get("egress_port", 1))
        self.thrift_port = int(params.get("thrift_port", 9090))
        self.device_id = int(params.get("device_id", 0))
        self.cli = str(params.get("switch_cli", "simple_switch_CLI"))

    def _read_register(self, name: str) -> int:
        result = subprocess.run(
            [self.cli, "--thrift-port", str(self.thrift_port)],
            input=f"register_read {name} 0\n",
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        match = re.search(rf"{re.escape(name)}\[0\]\s*=\s*(\d+)", result.stdout)
        if match is None:
            raise RuntimeError(f"Cannot observe {name}: {result.stdout} {result.stderr}")
        return int(match.group(1))

    def runTest(self) -> None:  # noqa: N802
        """Send real frames and verify exact output plus UNKNOWN and eligibility metadata."""
        for case in _cases():
            with self.subTest(case=case.name):
                LOGGER.info("parser case %s", case.name)
                testutils.send_packet(self, (self.device_id, self.ingress_port), case.frame)
                testutils.verify_packet(self, case.frame, (self.device_id, self.egress_port))
                testutils.verify_no_other_packets(self)
                self.assertEqual(self._read_register("parser_feature_valid"), case.feature_valid)
                self.assertEqual(self._read_register("parser_class"), int(TrafficClass.UNKNOWN))
