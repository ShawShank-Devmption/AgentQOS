"""P2.1 PTF forwarding tests for the isolated parser harness in docs/parser_completion.md."""

import logging
import re
import subprocess

import pytest

from common.contracts import TrafficClass
from tests.parser_cases import parser_cases

pytest.importorskip(
    "ptf.base_tests", reason="PTF runs against BMv2 in the Linux data-plane environment"
)
import ptf  # noqa: E402
from ptf import testutils  # noqa: E402
from ptf.base_tests import BaseTest  # noqa: E402

LOGGER = logging.getLogger(__name__)


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
        for case in parser_cases():
            with self.subTest(case=case.name):
                LOGGER.info("parser case %s", case.name)
                testutils.send_packet(self, (self.device_id, self.ingress_port), case.frame)
                testutils.verify_packet(self, case.frame, (self.device_id, self.egress_port))
                testutils.verify_no_other_packets(self)
                self.assertEqual(self._read_register("parser_feature_valid"), case.feature_valid)
                self.assertEqual(self._read_register("parser_class"), int(TrafficClass.UNKNOWN))
