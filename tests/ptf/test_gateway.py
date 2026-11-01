"""C1/G2 live gateway flood exclusion and independent directional congestion flags.

Run on fresh fifo_gateway / agent_gateway artifacts, LAN/WAN exposed to PTF as 1/4.
These checks do not establish I3 shaping or bypass recovery on real tunnel traffic.
"""

import logging
import time

import pytest
from scapy.layers.inet import IP, UDP
from scapy.layers.l2 import Ether

from common.contracts import (
    CONGESTION_FLAG_REGISTER,
    CONGESTION_HIGH_PACKETS,
    CONGESTION_LOW_PACKETS,
    GATEWAY_LAN_PORT,
    GATEWAY_WAN_PORT,
)
from controller.policy import push_policy
from controller.switch_api import SwitchApi
from harness.gateway import gateway_policy_commands

pytest.importorskip("ptf.base_tests", reason="gateway checks require real Linux BMv2")
import ptf  # noqa: E402
from ptf import testutils  # noqa: E402
from ptf.base_tests import BaseTest  # noqa: E402

logger = logging.getLogger(__name__)


class GatewayForwardingTest(BaseTest):
    """Flood/unknown traffic exits only the opposite interface under both systems."""

    def setUp(self) -> None:  # noqa: N802
        """Install only peer forwarding and optional fail-open class policy."""
        super().setUp()
        self.dataplane = ptf.dataplane_instance
        self.dataplane.flush()
        params = testutils.test_params_get()
        self.api = SwitchApi(thrift_port=int(params.get("thrift_port", 9090)))
        self.system = str(params.get("system", "ours"))
        if self.system not in {"fifo", "ours"}:
            raise ValueError("gateway PTF system must be fifo or ours")
        if self.system == "ours":
            push_policy(self.api, 20, None)
        self.lan_mac = "02:00:00:00:00:01"
        self.wan_mac = "02:00:00:00:00:02"
        self.api.execute(gateway_policy_commands(20, self.lan_mac, self.wan_mac))

    def _flags(self) -> tuple[int, int]:
        # §7.7: gateway ABI index 0 is LAN egress, index 1 is WAN egress.
        (lan,) = self.api.read_register(CONGESTION_FLAG_REGISTER, 0)
        (wan,) = self.api.read_register(CONGESTION_FLAG_REGISTER, 1)
        return lan, wan

    def _wait_flags(self, expected: tuple[int, int]) -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            flags = self._flags()
            if flags == expected:
                logger.info("C1 gateway flags LAN=%d WAN=%d", *flags)
                return
            time.sleep(0.02)
        self.fail(f"directional congestion flags did not reach {expected}")

    def runTest(self) -> None:  # noqa: N802
        """Verify both directions, broadcasts, unknown unicast and directional flag isolation."""
        for ingress, egress, source, peer in (
            (GATEWAY_LAN_PORT, GATEWAY_WAN_PORT, self.lan_mac, self.wan_mac),
            (GATEWAY_WAN_PORT, GATEWAY_LAN_PORT, self.wan_mac, self.lan_mac),
        ):
            for destination in (peer, "ff:ff:ff:ff:ff:ff", "02:00:00:00:00:99"):
                packet = bytes(
                    Ether(src=source, dst=destination)
                    / IP(src="10.42.0.10", dst="1.1.1.1")
                    / UDP(sport=40000, dport=443)
                )
                testutils.send_packet(self, ingress, packet)
                testutils.verify_packet(self, packet, egress)
                testutils.verify_no_other_packets(self)
        if self.system == "ours":
            self._directional_hysteresis()

    def _directional_hysteresis(self) -> None:
        # §7.7: a fast-draining WAN must never clear a still-congested LAN flag.
        self.api.execute(
            [f"set_queue_rate 10 {GATEWAY_LAN_PORT}", f"set_queue_rate 40 {GATEWAY_WAN_PORT}"]
        )
        count = CONGESTION_HIGH_PACKETS + CONGESTION_LOW_PACKETS + 1
        to_wan = bytes(Ether(src=self.lan_mac, dst=self.wan_mac) / IP() / UDP())
        to_lan = bytes(Ether(src=self.wan_mac, dst=self.lan_mac) / IP() / UDP())
        try:
            testutils.send_packet(self, GATEWAY_LAN_PORT, to_wan, count=count)
            testutils.send_packet(self, GATEWAY_WAN_PORT, to_lan, count=count)
            self._wait_flags((1, 1))
            self._wait_flags((1, 0))
            for _ in range(count):
                testutils.verify_packet(self, to_lan, GATEWAY_LAN_PORT, timeout=10)
                testutils.verify_packet(self, to_wan, GATEWAY_WAN_PORT, timeout=10)
            self._wait_flags((0, 0))
            testutils.verify_no_other_packets(self)
        finally:
            self.api.execute(
                [f"set_queue_rate 0 {GATEWAY_LAN_PORT}", f"set_queue_rate 0 {GATEWAY_WAN_PORT}"]
            )
