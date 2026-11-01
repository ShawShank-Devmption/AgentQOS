"""D4/P3.13 real BMv2 queue hysteresis and isolated meter-red behavior.

Run alone on a fresh agent_aware switch exposing client/target ports and Thrift.
Overrides and zero-rate meters are controlled test stimuli, never demo classifications
or production preset tuning. No controller may write state while this test runs.
"""

import logging
import time

import pytest
from scapy.layers.inet import IP, UDP
from scapy.layers.l2 import Ether

from common.contracts import (
    CLASS_TREATMENTS,
    CONGESTION_FLAG_REGISTER,
    CONGESTION_HIGH_PACKETS,
    CONGESTION_LOW_PACKETS,
    METER_AGENT_BULK,
    METER_AGENT_INTERACTIVE,
    PRESET_NORMAL,
    PRESET_PROTECTIVE,
    SENDER_SWITCH_PORTS,
    TARGET_SWITCH_PORT,
    TrafficClass,
)
from controller.policy import class_policy_commands, push_policy
from controller.switch_api import SwitchApi, meter_set_rates_command, register_write_command
from harness.runtime import install_l2_forwarding

pytest.importorskip("ptf.base_tests", reason="D4 requires real Linux BMv2 queues and meters")
import ptf  # noqa: E402
from ptf import testutils  # noqa: E402
from ptf.base_tests import BaseTest  # noqa: E402

logger = logging.getLogger(__name__)


class QosProtectionTest(BaseTest):
    """Test queue-induced hysteresis separately from deterministic red-meter decisions."""

    def setUp(self) -> None:  # noqa: N802
        """Own policy on the fresh switch; the PTF runner owns interfaces and dataplane."""
        super().setUp()
        self.dataplane = ptf.dataplane_instance
        self.dataplane.flush()
        params = testutils.test_params_get()
        self.client = int(params.get("client_port", SENDER_SWITCH_PORTS[0]))
        self.target = int(params.get("target_port", TARGET_SWITCH_PORT))
        self.api = SwitchApi(thrift_port=int(params.get("thrift_port", 9090)))
        push_policy(self.api, 20, None)
        install_l2_forwarding(self.api)

    def _flag(self) -> int:
        (value,) = self.api.read_register(CONGESTION_FLAG_REGISTER, 0)
        return value

    def _wait_flag(self, expected: int) -> None:
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            if self._flag() == expected:
                logger.info(
                    "D4 real_queue congestion_flag=%d HI=%d LO=%d",
                    expected,
                    CONGESTION_HIGH_PACKETS,
                    CONGESTION_LOW_PACKETS,
                )
                return
            time.sleep(0.02)
        self.fail(f"congestion flag did not reach {expected}")

    def _packet(self, label: TrafficClass, ecn: int = 0) -> tuple[bytes, bytes]:
        # §7.16: overrides isolate treatment from classifier accuracy in this PTF test only.
        self.api.apply_flow_overrides({(0x0A000001, 0x0A000064, 17, 40000, 443): label})
        packet = (
            Ether(src="00:00:00:00:00:01", dst="00:00:00:00:00:64")
            / IP(src="10.0.0.1", dst="10.0.0.100", tos=ecn)
            / UDP(sport=40000, dport=443)
        )
        expected = packet.copy()
        expected[IP].tos = (CLASS_TREATMENTS[label].dscp << 2) | ecn
        return bytes(packet), bytes(expected)

    def _queue_hysteresis(self) -> None:
        # §7.7: only real dequeue depth drives the transitions in this phase.
        self.api.execute(
            [
                register_write_command(CONGESTION_FLAG_REGISTER, 0, 0),
                f"set_queue_depth {4 * CONGESTION_HIGH_PACKETS} {self.target}",
                f"set_queue_rate 20 {self.target}",
            ]
        )
        packet, expected = self._packet(TrafficClass.HUMAN_INTERACTIVE)
        count = CONGESTION_HIGH_PACKETS + CONGESTION_LOW_PACKETS + 1
        testutils.send_packet(self, self.client, packet, count=count)
        self._wait_flag(1)
        for _ in range(count):
            testutils.verify_packet(self, expected, self.target, timeout=6)
        self._wait_flag(0)
        testutils.verify_no_other_packets(self)
        self.api.execute([f"set_queue_rate 0 {self.target}"])

    def _protective_selection(self) -> None:
        # §7.16: distinct test meter rates prove that the flag selects the protective index.
        # Normal remains the frozen preset; only the isolated protective test entry is red.
        self.api.execute(class_policy_commands(20))
        self.api.execute(
            [meter_set_rates_command(METER_AGENT_BULK, PRESET_PROTECTIVE, [(0.0, 0), (0.0, 0)])]
        )
        packet, expected = self._packet(TrafficClass.AGENT_BULK)
        for flag in (PRESET_PROTECTIVE, PRESET_NORMAL):
            self.api.execute([register_write_command(CONGESTION_FLAG_REGISTER, 0, flag)])
            testutils.send_packet(self, self.client, packet)
            if flag == PRESET_NORMAL:
                testutils.verify_packet(self, expected, self.target)
            testutils.verify_no_other_packets(self)
            logger.info(
                "D4 isolated_selection preset=%d forwarded=%d", flag, int(flag == PRESET_NORMAL)
            )

    def _red_treatments(self) -> None:
        # Zero tokens force red without changing frozen METER_PRESETS. These are test-only
        # installs on a fresh isolated switch and are restored in runTest's finally block.
        self.api.execute(
            [
                meter_set_rates_command(meter, preset, [(0.0, 0), (0.0, 0)])
                for meter in (METER_AGENT_INTERACTIVE, METER_AGENT_BULK)
                for preset in (PRESET_NORMAL, PRESET_PROTECTIVE)
            ]
        )
        for label, ecn in (
            (TrafficClass.UNKNOWN, 0),
            (TrafficClass.HUMAN_INTERACTIVE, 0),
            (TrafficClass.AGENT_INTERACTIVE, 0),
            (TrafficClass.AGENT_INTERACTIVE, 2),
            (TrafficClass.AGENT_BULK, 0),
            (TrafficClass.AGENT_BULK, 2),
        ):
            packet, expected = self._packet(label, ecn)
            # §7.7: force protective selection for one deterministic meter test, separately
            # from the real-queue phase above; this is not evidence of a live HI transition.
            self.api.execute([register_write_command(CONGESTION_FLAG_REGISTER, 0, 1)])
            dropped = label == TrafficClass.AGENT_BULK and ecn == 0
            testutils.send_packet(self, self.client, packet)
            if not dropped:
                if ecn and label in (TrafficClass.AGENT_INTERACTIVE, TrafficClass.AGENT_BULK):
                    marked = Ether(expected)
                    marked[IP].tos = (CLASS_TREATMENTS[label].dscp << 2) | 3
                    del marked[IP].chksum
                    expected = bytes(marked)
                testutils.verify_packet(self, expected, self.target)
            testutils.verify_no_other_packets(self)
            logger.info(
                "D4 isolated_red class=%s ecn=%d forwarded=%d meter_drops=%d",
                label.name,
                ecn,
                int(not dropped),
                int(dropped),
            )

    def runTest(self) -> None:  # noqa: N802
        """Require real queue transitions and zero interactive meter drops on red."""
        try:
            self._queue_hysteresis()
            self._protective_selection()
            self._red_treatments()
        finally:
            self.api.execute(class_policy_commands(20))
            self.api.execute(
                [
                    register_write_command(CONGESTION_FLAG_REGISTER, 0, 0),
                    f"set_queue_rate 0 {self.target}",
                ]
            )
