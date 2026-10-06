"""Tests for framework adapters and human traffic command plans."""

from pathlib import Path

import pytest

from harness.agents import autogen, browser_use, claude_mcp, playwright_agent
from harness.human.replay import (
    BrowsingCaptureConfig,
    ReplayConfig,
    tcpreplay_command,
    tshark_command,
)


def test_framework_adapters_expose_stable_unique_sources() -> None:
    assert {
        browser_use.FRAMEWORK_NAME,
        playwright_agent.FRAMEWORK_NAME,
        autogen.FRAMEWORK_NAME,
        claude_mcp.FRAMEWORK_NAME,
    } == {"browser-use", "playwright-agent", "autogen", "claude-mcp"}


def test_tcpreplay_command_preserves_timing_and_uses_argument_vector(tmp_path: Path) -> None:
    trace = tmp_path / "human.pcap"
    trace.write_bytes(b"pcap fixture")

    command = tcpreplay_command(
        ReplayConfig(pcap_path=trace, interface="h_human-eth0", multiplier=1.0, loops=2)
    )

    assert command == (
        "tcpreplay",
        "--intf1=h_human-eth0",
        "--multiplier=1.0",
        "--loop=2",
        str(trace),
    )


def test_browsing_capture_command_has_explicit_duration_and_filter(tmp_path: Path) -> None:
    output = tmp_path / "browsing.pcap"
    command = tshark_command(
        BrowsingCaptureConfig(
            output_path=output,
            interface="en0",
            duration_s=7_200,
            capture_filter="tcp or udp",
        )
    )

    assert command == (
        "tshark",
        "-i",
        "en0",
        "-a",
        "duration:7200",
        "-f",
        "tcp or udp",
        "-w",
        str(output),
    )


@pytest.mark.parametrize("interface", ["", "eth0;rm", "eth0 space", "../eth0"])
def test_traffic_commands_reject_unsafe_interface_names(
    tmp_path: Path,
    interface: str,
) -> None:
    trace = tmp_path / "human.pcap"
    trace.write_bytes(b"pcap fixture")
    with pytest.raises(ValueError, match="interface"):
        ReplayConfig(pcap_path=trace, interface=interface)
