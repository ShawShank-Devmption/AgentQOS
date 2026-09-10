"""Tests for orchestration-derived ground-truth labels."""

import csv
from decimal import Decimal
from ipaddress import IPv4Address
from pathlib import Path

import pytest
from scapy.all import IP, TCP, UDP, Ether, wrpcap

from common.contracts import LABEL_FIELDS, TrafficClass
from harness.capture import CaptureWindow, PcapInputError, label_pcap_flows, write_labels


def _timestamped_packet(packet: object, timestamp: str) -> object:
    packet.time = Decimal(timestamp)
    return packet


def test_label_pcap_flows_uses_source_and_orchestration_time(tmp_path: Path) -> None:
    pcap_path = tmp_path / "review_fixture.pcap"
    packets = [
        _timestamped_packet(
            Ether() / IP(src="10.0.0.1", dst="10.0.0.100") / TCP(sport=40_000, dport=443),
            "100.1",
        ),
        _timestamped_packet(
            Ether() / IP(src="10.0.0.1", dst="10.0.0.100") / TCP(sport=40_000, dport=443),
            "100.2",
        ),
        _timestamped_packet(
            Ether() / IP(src="10.0.0.2", dst="10.0.0.100") / UDP(sport=50_000, dport=53),
            "100.3",
        ),
        _timestamped_packet(
            Ether() / IP(src="10.0.0.100", dst="10.0.0.1") / TCP(sport=443, dport=40_000),
            "100.4",
        ),
    ]
    wrpcap(str(pcap_path), packets)
    windows = (
        CaptureWindow(
            source_ip=IPv4Address("10.0.0.1"),
            start_time_s=Decimal("100.0"),
            end_time_s=Decimal("101.0"),
            label=TrafficClass.AGENT_INTERACTIVE,
            source_framework="playwright-agent",
        ),
        CaptureWindow(
            source_ip=IPv4Address("10.0.0.2"),
            start_time_s=Decimal("100.0"),
            end_time_s=Decimal("101.0"),
            label=TrafficClass.HUMAN_INTERACTIVE,
            source_framework="browser-capture",
        ),
    )

    labels = label_pcap_flows(pcap_path, windows)

    assert [(label.src_ip, label.proto, label.label) for label in labels] == [
        ("10.0.0.1", 6, TrafficClass.AGENT_INTERACTIVE.value),
        ("10.0.0.2", 17, TrafficClass.HUMAN_INTERACTIVE.value),
    ]
    assert labels[0].pcap_file == "review_fixture.pcap"


def test_write_labels_uses_frozen_header(tmp_path: Path) -> None:
    pcap_path = tmp_path / "one_flow.pcap"
    wrpcap(
        str(pcap_path),
        [
            _timestamped_packet(
                Ether() / IP(src="10.0.0.1", dst="10.0.0.100") / TCP(sport=40_000, dport=443),
                "200.0",
            )
        ],
    )
    window = CaptureWindow(
        source_ip=IPv4Address("10.0.0.1"),
        start_time_s=Decimal("199.0"),
        end_time_s=Decimal("201.0"),
        label=TrafficClass.AGENT_BULK,
        source_framework="autogen",
    )
    labels = label_pcap_flows(pcap_path, (window,))
    output_path = tmp_path / "labels.csv"

    write_labels(labels, output_path)

    with output_path.open(encoding="utf-8", newline="") as output_file:
        reader = csv.DictReader(output_file)
        assert tuple(reader.fieldnames or ()) == LABEL_FIELDS
        assert list(reader)[0]["source_framework"] == "autogen"


def test_overlapping_windows_are_rejected(tmp_path: Path) -> None:
    pcap_path = tmp_path / "empty.pcap"
    wrpcap(str(pcap_path), [])
    windows = (
        CaptureWindow(
            IPv4Address("10.0.0.1"),
            Decimal("10"),
            Decimal("20"),
            TrafficClass.AGENT_INTERACTIVE,
            "browser-use",
        ),
        CaptureWindow(
            IPv4Address("10.0.0.1"),
            Decimal("20"),
            Decimal("30"),
            TrafficClass.AGENT_BULK,
            "browser-use",
        ),
    )

    with pytest.raises(PcapInputError, match="overlapping"):
        label_pcap_flows(pcap_path, windows)


def test_unknown_label_is_rejected() -> None:
    with pytest.raises(ValueError, match="UNKNOWN"):
        CaptureWindow(
            IPv4Address("10.0.0.1"),
            Decimal("1"),
            Decimal("2"),
            TrafficClass.UNKNOWN,
            "fixture",
        )
