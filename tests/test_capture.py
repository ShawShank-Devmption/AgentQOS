"""Tests for orchestration-derived ground-truth labels."""

import csv
from decimal import Decimal
from ipaddress import IPv4Address
from pathlib import Path

import pytest
from scapy.all import IP, TCP, UDP, Ether, wrpcap

from common.contracts import LABEL_FIELDS, TrafficClass
from harness.capture import (
    CaptureWindow,
    FlowLabel,
    PcapInputError,
    label_pcap_flows,
    read_capture_windows,
    verify_corpus,
    write_capture_window,
    write_labels,
)


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


def test_capture_windows_round_trip_through_jsonl(tmp_path: Path) -> None:
    log_path = tmp_path / "orchestration.jsonl"
    windows = (
        CaptureWindow(
            IPv4Address("10.0.0.1"),
            Decimal("100.125"),
            Decimal("101.875"),
            TrafficClass.AGENT_INTERACTIVE,
            "browser-use",
        ),
        CaptureWindow(
            IPv4Address("10.0.0.2"),
            Decimal("200"),
            Decimal("201"),
            TrafficClass.HUMAN_INTERACTIVE,
            "browser-capture",
        ),
    )
    for window in windows:
        write_capture_window(window, log_path)

    assert read_capture_windows(log_path) == windows


def test_malformed_orchestration_log_is_rejected(tmp_path: Path) -> None:
    log_path = tmp_path / "orchestration.jsonl"
    log_path.write_text('{"source_ip": "not-an-ip"}\n', encoding="utf-8")

    with pytest.raises(PcapInputError, match="line 1"):
        read_capture_windows(log_path)


def test_corpus_verification_reports_framework_counts_and_rate() -> None:
    windows = (
        CaptureWindow(
            IPv4Address("10.0.0.1"),
            Decimal("1"),
            Decimal("2"),
            TrafficClass.AGENT_INTERACTIVE,
            "browser-use",
        ),
    )
    labels = (
        _flow_label("10.0.0.1", TrafficClass.AGENT_INTERACTIVE, "browser-use", 40_000),
        _flow_label("10.0.0.9", TrafficClass.AGENT_BULK, "unverified", 40_001),
    )

    verification = verify_corpus(labels, windows)

    assert verification.total_flows == 2
    assert verification.verified_flows == 1
    assert verification.verification_rate == 0.5
    assert verification.framework_counts == {"browser-use": 1, "unverified": 1}


def _flow_label(
    source_ip: str,
    label: TrafficClass,
    framework: str,
    source_port: int,
) -> FlowLabel:
    return FlowLabel(
        flow_id=f"{source_ip}:{source_port}-10.0.0.100:443-6",
        src_ip=source_ip,
        dst_ip="10.0.0.100",
        proto=6,
        src_port=source_port,
        dst_port=443,
        label=label.value,
        source_framework=framework,
        pcap_file="capture.pcap",
    )
