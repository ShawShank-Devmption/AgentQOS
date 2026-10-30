"""Hand-created two-tap pcap evidence for P3.12, not a performance experiment."""

import json
from decimal import Decimal
from ipaddress import IPv4Address
from pathlib import Path

import pytest
from scapy.all import IP, TCP, UDP, Ether, Raw, rdpcap, wrpcap

from common.contracts import CLASS_TREATMENTS, TrafficClass
from eval import pcap_metrics
from eval.metrics import MeasurementError, summarize_observations
from harness.capture import CaptureWindow, label_pcap_flows, write_labels


def fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    ingress, egress = tmp_path / "ingress.pcap", tmp_path / "egress.pcap"
    sent, received = [], []
    for index in range(16):
        up = index % 2 == 0
        packet = (
            Ether(src="02:00:00:00:00:01", dst="02:00:00:00:00:02")
            / IP(
                src="10.0.0.1" if up else "10.0.0.100",
                dst="10.0.0.100" if up else "10.0.0.1",
                id=index,
            )
            / TCP(sport=40000 if up else 443, dport=443 if up else 40000, seq=index)
            / Raw(b"x" * 100)
        )
        packet.time = Decimal(100) + Decimal(index) / 100
        sent.append(packet)
        copy = packet.copy()
        copy.time = packet.time + Decimal(index + 1) / 1000
        copy[IP].tos = CLASS_TREATMENTS[TrafficClass.HUMAN_INTERACTIVE].dscp << 2 | 3
        copy[IP].ttl = 63
        received.append(copy)
    wrpcap(str(ingress), sent)
    wrpcap(str(egress), received)
    labels = tmp_path / "labels.csv"
    window = CaptureWindow(
        IPv4Address("10.0.0.1"),
        Decimal(99),
        Decimal(102),
        TrafficClass.HUMAN_INTERACTIVE,
        "orchestration-fixture",
    )
    write_labels(label_pcap_flows(ingress, (window,)), labels)
    target = tmp_path / "target.jsonl"
    target.write_text(
        json.dumps(
            {
                "source_ip": "10.0.0.1",
                "start_time_s": 100.1,
                "end_time_s": 100.2,
                "status": 200,
                "method": "tools/call",
                "tool": "search",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return ingress, egress, labels, target


def test_hand_checked_pair_survives_marking_and_joins_both_directions(tmp_path: Path) -> None:
    ingress, egress, labels, target = fixture(tmp_path)
    raw = pcap_metrics.observations_from_pcaps(ingress, egress, labels, 100.0, 101.0, target)
    assert len(raw["packets"]) == 16
    assert len({packet["packet_id"] for packet in raw["packets"]}) == 16
    assert [row["checkpoint"] for row in raw["classifications"]] == [6, 16]
    assert all(
        row["truth"] == row["predicted"] == int(TrafficClass.HUMAN_INTERACTIVE)
        for row in raw["classifications"]
    )
    summary = summarize_observations(raw)
    human = summary["classes"]["HUMAN_INTERACTIVE"]
    assert human["latency_ms"] == pytest.approx({"p50": 8.5, "p95": 15.25, "p99": 15.85})
    assert human["throughput_mbps"] == pytest.approx(16 * 154 * 8 / 1_000_000)
    assert summary["tool_calls"]["completion_ms"]["p99"] == pytest.approx(100.0)
    output = tmp_path / "observations.json"
    assert (
        pcap_metrics.main(
            [
                str(ingress),
                str(egress),
                str(labels),
                "--start-s",
                "100",
                "--end-s",
                "101",
                "--tool-log",
                str(target),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    assert json.loads(output.read_text())["packets"] == raw["packets"]


def test_missing_receipt_is_censored_and_missing_checkpoint_is_not_invented(tmp_path: Path) -> None:
    ingress, egress, labels, _ = fixture(tmp_path)
    received = list(rdpcap(str(egress)))
    del received[5]
    wrpcap(str(egress), received)
    raw = pcap_metrics.observations_from_pcaps(ingress, egress, labels, 100, 101)
    assert raw["packets"][5]["received_s"] is None
    assert [row["checkpoint"] for row in raw["classifications"]] == [16]
    assert summarize_observations(raw)["classes"]["HUMAN_INTERACTIVE"]["lost"] == 1


@pytest.mark.parametrize("change", ["duplicate", "clock", "unmatched", "truncated", "unordered"])
def test_ambiguous_or_invalid_captures_fail_fast(tmp_path: Path, change: str) -> None:
    ingress, egress, labels, _ = fixture(tmp_path)
    received = list(rdpcap(str(egress)))
    if change == "truncated":
        egress.write_bytes(egress.read_bytes()[:-8])
    else:
        if change == "duplicate":
            duplicate = received[-1].copy()
            duplicate.time += Decimal("0.01")
            received.append(duplicate)
        elif change == "clock":
            received[0].time = Decimal("99.999")
        elif change == "unmatched":
            received[-1][IP].id = 99
        else:
            received[-1].time = Decimal(99)
        wrpcap(str(egress), received)
    with pytest.raises(MeasurementError):
        pcap_metrics.observations_from_pcaps(ingress, egress, labels, 100, 101)


def test_failed_target_requests_are_not_successful_completions(tmp_path: Path) -> None:
    ingress, egress, labels, target = fixture(tmp_path)
    event = json.loads(target.read_text())
    event["status"] = 500
    target.write_text(json.dumps(event) + "\n", encoding="utf-8")
    raw = pcap_metrics.observations_from_pcaps(ingress, egress, labels, 100, 101, target)
    assert raw["tool_calls"][0]["end_s"] is None
    assert summarize_observations(raw)["tool_calls"]["completion_fraction"] == 0


def test_unrecognized_dscp_is_unknown_prediction_not_unknown_ground_truth(tmp_path: Path) -> None:
    ingress, egress, labels, _ = fixture(tmp_path)
    received = list(rdpcap(str(egress)))
    received[5][IP].tos = 63 << 2
    wrpcap(str(egress), received)
    raw = pcap_metrics.observations_from_pcaps(ingress, egress, labels, 100, 101)
    assert raw["classifications"][0]["predicted"] == int(TrafficClass.UNKNOWN)
    assert raw["classifications"][0]["truth"] == int(TrafficClass.HUMAN_INTERACTIVE)


def test_reordered_arrivals_keep_ingress_checkpoint_identity(tmp_path: Path) -> None:
    ingress, egress, labels, _ = fixture(tmp_path)
    received = list(rdpcap(str(egress)))
    received[5].time = Decimal("100.08")
    received[5][IP].tos = CLASS_TREATMENTS[TrafficClass.AGENT_BULK].dscp << 2
    received.sort(key=lambda packet: packet.time)
    wrpcap(str(egress), received)
    raw = pcap_metrics.observations_from_pcaps(ingress, egress, labels, 100, 101)
    assert raw["packets"][5]["received_s"] == 100.08
    assert raw["classifications"][0]["predicted"] == int(TrafficClass.AGENT_BULK)


def test_late_receipts_and_tool_completions_are_censored(tmp_path: Path) -> None:
    ingress, egress, labels, target = fixture(tmp_path)
    received = list(rdpcap(str(egress)))
    received[-1].time = Decimal("101.1")
    wrpcap(str(egress), received)
    event = json.loads(target.read_text())
    event["end_time_s"] = 101.2
    target.write_text(json.dumps(event) + "\n", encoding="utf-8")
    raw = pcap_metrics.observations_from_pcaps(ingress, egress, labels, 100, 101, target)
    assert raw["packets"][-1]["received_s"] is None
    assert raw["tool_calls"][0]["end_s"] is None
    assert [row["checkpoint"] for row in raw["classifications"]] == [6]


def test_window_does_not_restart_a_flow_checkpoint_count(tmp_path: Path) -> None:
    ingress, egress, labels, _ = fixture(tmp_path)
    raw = pcap_metrics.observations_from_pcaps(ingress, egress, labels, 100.07, 101)
    assert len(raw["packets"]) == 9
    assert [row["checkpoint"] for row in raw["classifications"]] == [16]


def test_unlabeled_flow_has_unknown_truth_and_no_classification(tmp_path: Path) -> None:
    ingress, egress, labels, _ = fixture(tmp_path)
    labels.write_text(labels.read_text().splitlines()[0] + "\n", encoding="utf-8")
    raw = pcap_metrics.observations_from_pcaps(ingress, egress, labels, 100, 101)
    assert all(row["traffic_class"] == int(TrafficClass.UNKNOWN) for row in raw["packets"])
    assert raw["classifications"] == []


@pytest.mark.parametrize("change", ["ingress_duplicate", "bad_ip_length", "fragment", "linktype"])
def test_bad_or_ambiguous_ingress_is_rejected(tmp_path: Path, change: str) -> None:
    ingress, egress, labels, _ = fixture(tmp_path)
    sent = list(rdpcap(str(ingress)))
    if change == "linktype":
        data = bytearray(ingress.read_bytes())
        data[20:24] = (101).to_bytes(4, "little")
        ingress.write_bytes(data)
    else:
        if change == "ingress_duplicate":
            sent[-1] = sent[-2].copy()
            sent[-1].time = Decimal("100.15")
        elif change == "bad_ip_length":
            sent[0][IP].len = 4096
        else:
            sent[0][IP].flags = "MF"
        wrpcap(str(ingress), sent)
    with pytest.raises(MeasurementError):
        pcap_metrics.observations_from_pcaps(ingress, egress, labels, 100, 101)


@pytest.mark.parametrize("change", ["duplicate_field", "boolean_time", "reverse_clock", "extra"])
def test_malformed_target_log_is_rejected(tmp_path: Path, change: str) -> None:
    ingress, egress, labels, target = fixture(tmp_path)
    event = json.loads(target.read_text())
    if change == "duplicate_field":
        target.write_text(target.read_text().replace("{", '{"status": 500,', 1), encoding="utf-8")
    else:
        if change == "boolean_time":
            event["start_time_s"] = True
        elif change == "reverse_clock":
            event["end_time_s"] = 100.0
        else:
            event["label"] = 1
        target.write_text(json.dumps(event) + "\n", encoding="utf-8")
    with pytest.raises(MeasurementError):
        pcap_metrics.observations_from_pcaps(ingress, egress, labels, 100, 101, target)


def test_cli_preserves_existing_output_and_reports_failure(tmp_path: Path) -> None:
    ingress, egress, labels, _ = fixture(tmp_path)
    output = tmp_path / "observations.json"
    output.write_text("preserve\n", encoding="utf-8")
    assert (
        pcap_metrics.main(
            [
                str(ingress),
                str(egress),
                str(labels),
                "--start-s",
                "100",
                "--end-s",
                "101",
                "--output",
                str(output),
            ]
        )
        == 1
    )
    assert output.read_text() == "preserve\n"


def test_udp_join_ignores_l2_and_transport_checksum_rewrites(tmp_path: Path) -> None:
    ingress, egress, labels, _ = fixture(tmp_path)
    sent, received = [], []
    for index in range(6):
        packet = (
            Ether(src="02:00:00:00:00:01", dst="02:00:00:00:00:02")
            / IP(src="10.0.0.1", dst="10.0.0.100", id=index)
            / UDP(sport=40000, dport=443)
            / Raw(bytes([index]) * 8)
        )
        packet.time = Decimal(100) + Decimal(index) / 100
        sent.append(packet)
        arrival = packet.copy()
        arrival.time += Decimal("0.005")
        arrival[Ether].src = "02:00:00:00:00:03"
        arrival[Ether].dst = "02:00:00:00:00:04"
        arrival[IP].chksum = 0x1234
        arrival[UDP].chksum = 0x5678
        arrival[IP].tos = CLASS_TREATMENTS[TrafficClass.AGENT_INTERACTIVE].dscp << 2
        received.append(arrival)
    wrpcap(str(ingress), sent)
    wrpcap(str(egress), received)
    window = CaptureWindow(
        IPv4Address("10.0.0.1"),
        Decimal(99),
        Decimal(102),
        TrafficClass.AGENT_INTERACTIVE,
        "orchestration-fixture",
    )
    write_labels(label_pcap_flows(ingress, (window,)), labels)
    raw = pcap_metrics.observations_from_pcaps(ingress, egress, labels, 100, 101)
    assert raw["classifications"][0]["checkpoint"] == 6
    assert raw["classifications"][0]["predicted"] == int(TrafficClass.AGENT_INTERACTIVE)
    measured = summarize_observations(raw)["classes"]["AGENT_INTERACTIVE"]
    assert measured["latency_ms"] == pytest.approx({"p50": 5, "p95": 5, "p99": 5})
    assert measured["throughput_mbps"] == pytest.approx(6 * 50 * 8 / 1_000_000)
