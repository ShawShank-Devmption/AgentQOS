"""P4.1 live BMv2 checks for the static-port DiffServ baseline."""

import pytest
from scapy.layers.inet import IP, TCP
from scapy.layers.l2 import Ether

from common.contracts import TrafficClass
from controller.policy import class_policy_commands
from controller.switch_api import SwitchApi
from eval.baselines.diffserv import DiffServConfig, PortRule

pytest.importorskip("ptf.base_tests", reason="PTF requires the Linux BMv2 test environment")
import ptf  # noqa: E402
from ptf import testutils  # noqa: E402
from ptf.base_tests import BaseTest  # noqa: E402


class DiffServPortTreatmentTest(BaseTest):
    """Observe class DSCP marking in both directions and UNKNOWN best effort."""

    def setUp(self) -> None:  # noqa: N802
        """Install static port and shared class policy on the isolated test switch."""
        super().setUp()
        # PTF's BaseTest owns no dataplane; tests send through the runner's instance.
        self.dataplane = ptf.dataplane_instance
        self.dataplane.flush()
        params = testutils.test_params_get()
        self.client_port = int(params.get("client_port", 1))
        self.target_port = int(params.get("target_port", 4))
        self.api = SwitchApi(thrift_port=int(params.get("thrift_port", 9090)))
        self.api.execute(class_policy_commands(20))
        config = DiffServConfig((PortRule("tcp", 443, TrafficClass.HUMAN_INTERACTIVE),))
        self.api.execute(config.port_rule_commands())
        self.api.execute(
            [
                "table_add tbl_l2_forward set_egress_port 00:00:00:00:00:02 => 4",
                "table_add tbl_l2_forward set_egress_port 00:00:00:00:00:01 => 1",
            ]
        )

    def runTest(self) -> None:  # noqa: N802
        """Verify same static application port marks requests and responses."""
        client = "00:00:00:00:00:01"
        target = "00:00:00:00:00:02"
        request = (
            Ether(src=client, dst=target)
            / IP(src="10.0.0.1", dst="10.0.0.100")
            / TCP(sport=40000, dport=443)
        )
        response = (
            Ether(src=target, dst=client)
            / IP(src="10.0.0.100", dst="10.0.0.1")
            / TCP(sport=443, dport=40000)
        )
        unmatched = (
            Ether(src=client, dst=target)
            / IP(src="10.0.0.1", dst="10.0.0.100")
            / TCP(sport=40001, dport=444)
        )
        for ingress, egress, packet, dscp in (
            (self.client_port, self.target_port, request, 34),
            (self.target_port, self.client_port, response, 34),
            (self.client_port, self.target_port, unmatched, 0),
        ):
            expected = packet.copy()
            expected[IP].tos = dscp << 2
            del expected[IP].chksum
            testutils.send_packet(self, ingress, bytes(packet))
            testutils.verify_packet(self, bytes(expected), egress)
            testutils.verify_no_other_packets(self)
