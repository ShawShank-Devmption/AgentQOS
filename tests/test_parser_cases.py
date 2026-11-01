"""C2: parser fail-open frames reach BMv2 through Linux AF_PACKET."""

from scapy.layers.l2 import Ether

from tests.parser_cases import parser_cases


def test_fail_open_cases_have_sendable_ethernet_headers() -> None:
    cases = parser_cases()
    assert len(cases) >= 24
    assert len({case.name for case in cases}) == len(cases)
    assert all(len(case.frame) >= 14 for case in cases)
    truncated = next(case for case in cases if case.name == "ethernet_only_ipv4")
    assert len(truncated.frame) == 14
    assert Ether(truncated.frame).type == 0x0800
    assert truncated.feature_valid == 0
    assert next(case for case in cases if case.name == "partial_ipv4").feature_valid == 0
