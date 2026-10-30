"""Tests for gateway capture sessions: tcpdump orchestration plus orchestration-window labels."""

import csv
import json
import os
import time
from decimal import Decimal
from ipaddress import IPv4Address
from pathlib import Path

import pytest
from scapy.all import IP, TCP, UDP, Ether, wrpcap

from common.contracts import LABEL_FIELDS, TrafficClass
from harness.capture import CaptureSessionError, start_session, stop_session, tcpdump_command

DEVICE = IPv4Address("10.42.0.10")
FAKE_TCPDUMP = """#!/bin/sh
if [ -n "$FAKE_TCPDUMP_FAIL" ]; then
	echo "tcpdump: gw-lan-sw: No such device exists" >&2
	exit 1
fi
echo "listening on gw-lan-sw, link-type EN10MB (Ethernet), snapshot length 262144 bytes" >&2
trap 'printf "5 packets captured\\n5 packets received by filter\\n%s packets dropped by kernel\\n" \
"${FAKE_TCPDUMP_DROPS:-0}" >&2; exit 0' TERM
while :; do sleep 0.05; done
"""


@pytest.fixture
def fake_tcpdump(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "tcpdump"
    script.write_text(FAKE_TCPDUMP)
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    return script


def _packet(packet: object) -> object:
    packet.time = Decimal(repr(time.time()))
    return packet


def _write_session_pcap(session: Path) -> None:
    """Device flows: TCP opened in the capture, TCP joined mid-stream, UDP; plus a reply."""
    server = "142.250.1.1"
    wrpcap(
        str(session / "capture.pcap"),
        [
            _packet(Ether() / IP(src=str(DEVICE), dst=server) / TCP(sport=40001, dport=443)),
            _packet(Ether() / IP(src=server, dst=str(DEVICE)) / TCP(sport=443, dport=40001)),
            _packet(
                Ether() / IP(src=str(DEVICE), dst=server) / TCP(sport=40002, dport=443, flags="A")
            ),
            _packet(Ether() / IP(src=str(DEVICE), dst=server) / UDP(sport=50001, dport=443)),
        ],
    )


def _start(session: Path, label: TrafficClass = TrafficClass.HUMAN_INTERACTIVE) -> int:
    return start_session(session, "gw-lan-sw", DEVICE, label, "human-video-call")


def test_tcpdump_command_captures_full_packets_of_one_device(tmp_path: Path) -> None:
    command = tcpdump_command("gw-lan-sw", DEVICE, tmp_path / "capture.pcap")
    assert command[0] == "tcpdump"
    assert command[command.index("-i") + 1] == "gw-lan-sw"
    assert command[command.index("-s") + 1] == "0"
    assert "-U" in command
    assert command[command.index("-w") + 1] == str(tmp_path / "capture.pcap")
    assert command[-2:] == ["host", "10.42.0.10"]


def test_session_labels_device_flows_and_excludes_midstream_tcp(
    tmp_path: Path, fake_tcpdump: Path
) -> None:
    session = tmp_path / "corpus" / "call-1"
    _start(session)
    manifest = json.loads((session / "session.json").read_text())
    assert manifest["status"] == "capturing"
    assert manifest["label"] == "HUMAN_INTERACTIVE"
    _write_session_pcap(session)

    labels = stop_session(session)

    assert [label.src_port for label in labels] == [40001, 50001]
    assert {label.label for label in labels} == {int(TrafficClass.HUMAN_INTERACTIVE)}
    with (session / "labels.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert tuple(rows[0]) == LABEL_FIELDS
    assert {row["pcap_file"] for row in rows} == {"capture.pcap"}
    manifest = json.loads((session / "session.json").read_text())
    assert manifest["status"] == "complete"
    assert manifest["labelled_flows"] == 2
    assert manifest["excluded_midstream_tcp_flows"] == 1
    assert manifest["dropped_by_kernel"] == 0
    assert Decimal(manifest["end_time_s"]) > Decimal(manifest["start_time_s"])


def test_existing_session_directory_is_never_reused(tmp_path: Path, fake_tcpdump: Path) -> None:
    session = tmp_path / "call-1"
    session.mkdir()
    with pytest.raises(FileExistsError):
        _start(session)


def test_unknown_label_is_rejected_before_capturing(tmp_path: Path, fake_tcpdump: Path) -> None:
    with pytest.raises(ValueError, match="UNKNOWN"):
        _start(tmp_path / "s", TrafficClass.UNKNOWN)
    assert not (tmp_path / "s" / "capture.pcap").exists()


def test_tcpdump_failing_to_start_is_reported(
    tmp_path: Path, fake_tcpdump: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_TCPDUMP_FAIL", "1")
    with pytest.raises(CaptureSessionError, match="No such device"):
        _start(tmp_path / "s")
    assert json.loads((tmp_path / "s" / "session.json").read_text())["status"] == "failed"


def test_kernel_drops_fail_the_session_without_labels(
    tmp_path: Path, fake_tcpdump: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_TCPDUMP_DROPS", "3")
    session = tmp_path / "s"
    _start(session)
    _write_session_pcap(session)
    with pytest.raises(CaptureSessionError, match="dropped 3"):
        stop_session(session)
    assert not (session / "labels.csv").exists()
    manifest = json.loads((session / "session.json").read_text())
    assert manifest["status"] == "failed"
    assert manifest["dropped_by_kernel"] == 3


def test_session_cannot_be_stopped_twice(tmp_path: Path, fake_tcpdump: Path) -> None:
    session = tmp_path / "s"
    _start(session)
    _write_session_pcap(session)
    stop_session(session)
    with pytest.raises(CaptureSessionError, match="not capturing"):
        stop_session(session)
