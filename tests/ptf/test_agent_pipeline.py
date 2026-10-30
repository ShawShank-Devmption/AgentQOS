"""Live P3.9/P3.10/P3.13 BMv2 smoke for clone, treatment and fail-open state.

Run with a fresh agent_aware.json switch exposing PTF ports 1, 4 and 255, plus Thrift.
The test owns policy/table entries but the PTF harness owns the switch and interfaces.
"""

import ipaddress
import struct

import pytest
from scapy.layers.inet import IP, TCP
from scapy.layers.l2 import Ether
from scapy.packet import Raw

from common.contracts import (
    CLASS_TREATMENTS,
    CM_SKETCH_COLUMNS,
    CM_SKETCH_REGISTERS,
    CPU_CLONE_SESSION,
    CPU_PORT,
    PROTO_META_EXT_COUNT_SHIFT,
    PROTO_META_PAYLOAD_SEEN,
    PUNT_REASON_FIRST_PACKET,
    AlpnClass,
    TrafficClass,
)
from common.feature_math import flow_key_bytes, flow_slot, flow_tag, sketch_columns
from controller.policy import push_policy
from controller.switch_api import SwitchApi
from harness.runtime import install_l2_forwarding
from tests.test_ja4 import client_hello

pytest.importorskip("ptf.base_tests", reason="PTF requires Linux BMv2 and three exposed ports")
import ptf  # noqa: E402
from ptf import testutils  # noqa: E402
from ptf.base_tests import BaseTest  # noqa: E402


class AgentPipelineSmokeTest(BaseTest):
    """Verify the frozen CPU format and data-plane effects on real BMv2 packets."""

    def setUp(self) -> None:  # noqa: N802
        """Install policy on a fresh switch with the three required PTF ports."""
        super().setUp()
        # PTF's BaseTest owns no dataplane; tests send through the runner's instance.
        self.dataplane = ptf.dataplane_instance
        self.dataplane.flush()
        params = testutils.test_params_get()
        self.device_id = int(params.get("device_id", 0))
        self.client_port = int(params.get("client_port", 1))
        self.target_port = int(params.get("target_port", 4))
        self.cpu_port = int(params.get("cpu_port", CPU_PORT))
        self.api = SwitchApi(thrift_port=int(params.get("thrift_port", 9090)))
        push_policy(self.api, 20, None)
        install_l2_forwarding(self.api)
        self.api.execute([f"mirroring_add {CPU_CLONE_SESSION} {self.cpu_port}"])

    def _port(self, number: int) -> tuple[int, int]:
        return self.device_id, number

    def _read(self, register: str, index: int) -> int:
        values = self.api.read_register(register, index)
        self.assertEqual(len(values), 1)
        return values[0]

    def _assert_sketch(self, source: int, expected: int) -> None:
        # §7.10: this fresh, controller-free switch stays in epoch 0 during the test.
        # Each distinct new flow from this source increments every row exactly once.
        for row, column in enumerate(sketch_columns(source)):
            self.assertEqual(
                self._read(CM_SKETCH_REGISTERS[0], row * CM_SKETCH_COLUMNS + column), expected
            )

    def runTest(self) -> None:  # noqa: N802
        """Exercise clone metadata, TLS/sketch state, override DSCP and fragment fallback."""
        client_mac = "00:00:00:00:00:01"
        target_mac = "00:00:00:00:00:64"
        client_ip = "10.0.0.1"
        target_ip = "10.0.0.100"
        key = flow_key_bytes(
            int(ipaddress.IPv4Address(client_ip)),
            int(ipaddress.IPv4Address(target_ip)),
            6,
            40000,
            443,
        )
        slot = flow_slot(key)
        hello = (
            Ether(src=client_mac, dst=target_mac)
            / IP(src=client_ip, dst=target_ip)
            / TCP(sport=40000, dport=443, flags="PA")
            / Raw(client_hello())
        )
        original = bytes(hello)
        source = int(ipaddress.IPv4Address(client_ip))
        self._assert_sketch(source, 0)
        cpu = struct.pack(
            "!HBBHH",
            slot,
            int(TrafficClass.UNKNOWN),
            PUNT_REASON_FIRST_PACKET,
            self.client_port,
            0,
        )
        testutils.send_packet(self, self._port(self.client_port), original)
        testutils.verify_packet(self, original, self._port(self.target_port))
        testutils.verify_packet(self, cpu + original, self._port(self.cpu_port))
        testutils.verify_no_other_packets(self)
        self.assertEqual(self._read("reg_pkt_count", slot), 1)
        self._assert_sketch(source, 1)

        # §7.10: count a second valid flow before testing malformed TLS separately.
        second_flow = (
            Ether(src=client_mac, dst=target_mac)
            / IP(src=client_ip, dst=target_ip)
            / TCP(sport=40002, dport=443, flags="PA")
            / Raw(b"plain TCP payload")
        )
        testutils.send_packet(self, self._port(self.client_port), bytes(second_flow))
        testutils.verify_packet(self, bytes(second_flow), self._port(self.target_port))
        testutils.verify_no_other_packets(self)
        self._assert_sketch(source, 2)

        # §7.15: malformed TLS forwards without claiming state or emitting a punt.
        trailing = bytearray(client_hello())
        trailing[3:5] = (int.from_bytes(trailing[3:5], "big") + 1).to_bytes(2, "big")
        trailing[6:9] = (int.from_bytes(trailing[6:9], "big") + 1).to_bytes(3, "big")
        trailing.extend(b"x")
        for port, payload in enumerate((b"\x16\x03" + b"\x00" * 42, bytes(trailing)), start=40003):
            malformed = (
                Ether(src=client_mac, dst=target_mac)
                / IP(src=client_ip, dst=target_ip)
                / TCP(sport=port, dport=443, flags="PA")
                / Raw(payload)
            )
            malformed_key = flow_key_bytes(
                int(ipaddress.IPv4Address(client_ip)),
                int(ipaddress.IPv4Address(target_ip)),
                6,
                port,
                443,
            )
            testutils.send_packet(self, self._port(self.client_port), bytes(malformed))
            testutils.verify_packet(self, bytes(malformed), self._port(self.target_port))
            testutils.verify_no_other_packets(self)
            self.assertEqual(self._read("reg_proto_meta", flow_slot(malformed_key)), 0)
            self.assertEqual(self._read("reg_pkt_count", flow_slot(malformed_key)), 0)
            self.assertEqual(self._read("reg_flow_key", flow_slot(malformed_key)), 0)
        self.assertEqual(self._read("reg_flow_key", slot), flow_tag(key))
        self.assertEqual(
            self._read("reg_proto_meta", slot),
            PROTO_META_PAYLOAD_SEEN | (4 << PROTO_META_EXT_COUNT_SHIFT) | int(AlpnClass.H2),
        )
        # §7.10/§7.15: only the valid flows counted; malformed TLS cannot add a new flow.
        self._assert_sketch(source, 2)

        # §7.15: a fragment forwards with UNKNOWN treatment and cannot touch flow registers.
        fragment = (
            Ether(src=client_mac, dst=target_mac)
            / IP(src=client_ip, dst=target_ip, flags="MF")
            / TCP(sport=40000, dport=443)
        )
        testutils.send_packet(self, self._port(self.client_port), bytes(fragment))
        testutils.verify_packet(self, bytes(fragment), self._port(self.target_port))
        testutils.verify_no_other_packets(self)
        self.assertEqual(self._read("reg_pkt_count", slot), 1)
        self._assert_sketch(source, 2)

        # §7.16: exact HUMAN override changes DSCP without affecting forwarding.
        self.api.apply_flow_overrides(
            {
                (
                    source,
                    int(ipaddress.IPv4Address(target_ip)),
                    6,
                    40001,
                    443,
                ): TrafficClass.HUMAN_INTERACTIVE
            }
        )
        classified = (
            Ether(src=client_mac, dst=target_mac)
            / IP(src=client_ip, dst=target_ip)
            / TCP(sport=40001, dport=443)
        )
        expected = classified.copy()
        expected[IP].tos = CLASS_TREATMENTS[TrafficClass.HUMAN_INTERACTIVE].dscp << 2
        del expected[IP].chksum
        testutils.send_packet(self, self._port(self.client_port), bytes(classified))
        testutils.verify_packet(self, bytes(expected), self._port(self.target_port))
        testutils.verify_no_other_packets(self)
