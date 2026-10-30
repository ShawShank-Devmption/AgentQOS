"""Validation and subprocess cleanup for timing-faithful trace replay."""

import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest
from scapy.all import IP, UDP, Ether, wrpcap

from harness.human.replay import ReplayError, replay_trace, validate_trace


def test_trace_validation_rejects_unreadable_and_empty(tmp_path: Path) -> None:
    path = tmp_path / "trace.pcap"
    with pytest.raises(FileNotFoundError):
        validate_trace(path)
    path.write_bytes(b"not a pcap")
    with pytest.raises(ReplayError):
        validate_trace(path)


def test_replay_exact_command_and_exclusive_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trace = tmp_path / "trace with spaces.pcap"
    wrpcap(str(trace), [Ether() / IP() / UDP()])
    runner = Mock(return_value=subprocess.CompletedProcess([], 0))
    monkeypatch.setattr("harness.human.replay.subprocess.run", runner)
    output = tmp_path / "replay.log"
    replay_trace(trace, "h_human-eth0", output, timeout_s=3)
    assert runner.call_args.args[0] == [
        "tcpreplay",
        "--intf1",
        "h_human-eth0",
        "--multiplier",
        "1.0",
        str(trace.resolve()),
    ]
    assert runner.call_args.kwargs["timeout"] == 3
    with pytest.raises(FileExistsError):
        replay_trace(trace, "h_human-eth0", output, timeout_s=3)


@pytest.mark.parametrize("interface", ["", "-eth0", "eth0;rm -rf /", "a" * 16])
def test_interface_validation(interface: str, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        replay_trace(tmp_path / "trace", interface, tmp_path / "out", timeout_s=3)


def test_replay_failure_preserves_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trace = tmp_path / "trace.pcap"
    wrpcap(str(trace), [Ether() / IP() / UDP()])
    monkeypatch.setattr(
        "harness.human.replay.subprocess.run", Mock(return_value=subprocess.CompletedProcess([], 2))
    )
    with pytest.raises(ReplayError, match="exit status 2"):
        replay_trace(trace, "eth0", tmp_path / "output", timeout_s=3)


@pytest.mark.parametrize("damage", ["record_header", "record_body", "snaplen", "original"])
def test_trace_validation_rejects_truncated_capture(tmp_path: Path, damage: str) -> None:
    import struct

    trace = tmp_path / "truncated.pcap"
    packet = bytes(Ether() / IP() / UDP())
    global_header = struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1)
    record = struct.pack("<IIII", 1, 0, len(packet), len(packet))
    valid_record = record + packet
    if damage == "record_header":
        content = global_header + valid_record + record[:8]
    elif damage == "record_body":
        content = global_header + valid_record + record + packet[:-1]
    elif damage == "snaplen":
        content = struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 16, 1) + valid_record
    else:
        content = global_header + struct.pack("<IIII", 1, 0, len(packet), len(packet) + 1) + packet
    trace.write_bytes(content)
    with pytest.raises(ReplayError):
        validate_trace(trace)


def test_trace_validation_requires_pcapng_conversion(tmp_path: Path) -> None:
    from scapy.all import PcapNgWriter

    trace = tmp_path / "trace.pcapng"
    with PcapNgWriter(str(trace)) as writer:
        writer.write(Ether() / IP() / UDP())
    with pytest.raises(ReplayError, match="convert pcapng"):
        validate_trace(trace)
