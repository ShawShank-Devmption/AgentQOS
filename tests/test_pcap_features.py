"""P3.3: real pcap/ground-truth ingestion and bit-exact checkpoint preprocessing."""

from decimal import Decimal
from ipaddress import IPv4Address
from pathlib import Path

import pytest
from scapy.all import IP, TCP, Ether, Raw, wrpcap

from common.contracts import FEATURE_ORDER, TrafficClass
from harness.capture import CaptureWindow, PcapInputError, label_pcap_flows, write_labels
from ml.extract_features import (
    CmSketch,
    PacketObservation,
    extract_features,
    extract_flows,
    replay_with_switch_timestamps,
)


def _corpus(tmp_path: Path, count: int = 16) -> tuple[Path, Path]:
    pcap = tmp_path / "capture.pcap"
    labels = tmp_path / "labels.csv"
    packets = []
    for i in range(count):
        upstream = i % 2 == 0
        src, dst = ("10.0.0.1", "10.0.0.100") if upstream else ("10.0.0.100", "10.0.0.1")
        sport, dport = (40000, 443) if upstream else (443, 40000)
        packet = Ether() / IP(src=src, dst=dst) / TCP(sport=sport, dport=dport) / Raw(b"x" * 100)
        packet.time = Decimal("100") + Decimal(i) / 1000
        packets.append(packet)
    wrpcap(str(pcap), packets)
    windows = (
        CaptureWindow(
            IPv4Address("10.0.0.1"),
            Decimal(99),
            Decimal(102),
            TrafficClass.AGENT_INTERACTIVE,
            "real-runner",
        ),
    )
    write_labels(label_pcap_flows(pcap, windows), labels)
    return pcap, labels


def test_pcap_bridge_combines_both_directions_and_frozen_checkpoints(tmp_path: Path) -> None:
    pcap, labels = _corpus(tmp_path)
    flows = extract_flows(pcap)
    assert len(flows) == 1
    assert [packet.upstream for packet in next(iter(flows.values()))][:4] == [True, False] * 2
    frame = extract_features(pcap, labels)
    assert list(frame.columns) == [
        "flow_id",
        "checkpoint",
        *FEATURE_ORDER,
        "label",
        "source_framework",
    ]
    assert frame["checkpoint"].tolist() == [6, 16]
    assert frame["iat_ewma_us"].tolist() == [1000, 1000]
    assert frame["fanout_new_flows"].tolist() == [1, 1]
    assert frame["source_framework"].tolist() == ["real-runner", "real-runner"]


def test_label_orientation_is_preserved_when_response_is_first(tmp_path: Path) -> None:
    pcap, labels = _corpus(tmp_path)
    from scapy.all import rdpcap

    packets = list(rdpcap(str(pcap)))
    packets[0] = packets[1].copy()
    packets[0].time = Decimal(100)
    wrpcap(str(pcap), packets)
    frame = extract_features(pcap, labels)
    assert frame.iloc[0]["mean_pkt_size_up"] == (2 * 154) >> 2


def test_sparse_sketch_counts_new_flows_and_clears_on_epoch_change() -> None:
    sketch = CmSketch()
    source = int(IPv4Address("10.0.0.1"))
    for now in (100, 200, 300):
        sketch.claim(source, now)
    assert sketch.estimate(source, 300) == 3
    assert sketch.estimate(source, 1_000_100) == 0
    sketch.claim(source, 1_000_200)
    assert sketch.estimate(source, 1_000_300) == 1
    assert sketch.estimate(source, 3_000_100) == 0


def test_truncated_record_is_rejected_instead_of_training_on_partial_capture(
    tmp_path: Path,
) -> None:
    pcap, labels = _corpus(tmp_path)
    pcap.write_bytes(pcap.read_bytes()[:-10])
    with pytest.raises(PcapInputError, match="truncat"):
        extract_features(pcap, labels)


def test_rejects_unordered_capture_timestamps(tmp_path: Path) -> None:
    pcap, labels = _corpus(tmp_path)
    from scapy.all import rdpcap

    packets = list(rdpcap(str(pcap)))
    packets[-1].time = Decimal(99)
    wrpcap(str(pcap), packets)
    with pytest.raises(PcapInputError, match="timestamp"):
        extract_features(pcap, labels)


def test_small_capture_reordering_keeps_file_order_with_zero_gap(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Multi-CPU receive paths stamp packets out of order by microseconds (real gateway: <300 us).

    The switch stamps in arrival (file) order with its own monotonic clock, so a small step back
    becomes a zero gap rather than a reordering or a rejected capture.
    """
    pcap, labels = _corpus(tmp_path)
    from scapy.all import rdpcap

    packets = list(rdpcap(str(pcap)))
    packets[3].time = packets[2].time - Decimal("0.000300")
    wrpcap(str(pcap), packets)
    flows = extract_flows(pcap)
    timestamps = [packet.timestamp_us for packet in next(iter(flows.values()))]
    assert timestamps[3] == timestamps[2]
    assert timestamps == sorted(timestamps)
    assert "clamped 1 capture timestamp" in caplog.text
    assert extract_features(pcap, labels)["checkpoint"].tolist() == [6, 16]


def test_capture_reordering_beyond_tolerance_is_rejected(tmp_path: Path) -> None:
    pcap, labels = _corpus(tmp_path)
    from scapy.all import rdpcap

    packets = list(rdpcap(str(pcap)))
    packets[3].time = packets[2].time - Decimal("0.005")
    wrpcap(str(pcap), packets)
    with pytest.raises(PcapInputError, match="timestamp"):
        extract_features(pcap, labels)


def test_unknown_and_duplicate_ground_truth_rows_are_rejected(tmp_path: Path) -> None:
    pcap, labels = _corpus(tmp_path)
    text = labels.read_text()
    labels.write_text(text.replace(",2,real-runner,", ",0,real-runner,"))
    with pytest.raises(PcapInputError, match="label"):
        extract_features(pcap, labels)
    labels.write_text(text + text.splitlines()[1] + "\n")
    with pytest.raises(PcapInputError, match="duplicate"):
        extract_features(pcap, labels)


def test_switch_timestamp_replay_validates_alignment_and_replaces_pcap_clock() -> None:
    packets = [PacketObservation(i * 1000, 100, True) for i in range(6)]
    states = replay_with_switch_timestamps(packets, [i * 2000 for i in range(6)])
    assert states[-1].iat_ewma == 2000
    assert states[-1].pkt_count == 6
    assert states[0].pkt_count == 1
    with pytest.raises(ValueError, match="timestamp"):
        replay_with_switch_timestamps(packets, [1])


def test_fragments_do_not_update_flow_state(tmp_path: Path) -> None:
    pcap, _ = _corpus(tmp_path)
    fragment = Ether() / IP(src="10.0.0.3", dst="10.0.0.100", frag=1) / Raw(b"x" * 40)
    fragment.time = Decimal(100)
    wrpcap(str(pcap), [fragment])
    assert extract_flows(pcap) == {}


def test_ethernet_padding_does_not_consume_first_tls_payload(tmp_path: Path) -> None:
    from scapy.all import Padding

    from ml.extract_features import FlowState, update_flow
    from tests.test_feature_math import _alpn, _client_hello

    pcap = tmp_path / "padding.pcap"
    hello = _client_hello([(0x0010, _alpn(b"h2"))])
    syn = (
        Ether()
        / IP(src="10.0.0.1", dst="10.0.0.100", len=40)
        / TCP(sport=40000, dport=443, flags="S")
        / Padding(b"\x00" * 6)
    )
    data = Ether() / IP(src="10.0.0.1", dst="10.0.0.100") / TCP(sport=40000, dport=443) / Raw(hello)
    syn.time, data.time = Decimal(100), Decimal("100.01")
    wrpcap(str(pcap), [syn, data])
    packets = next(iter(extract_flows(pcap).values()))
    assert packets[0].payload == b""
    state = FlowState()
    for packet in packets:
        update_flow(state, packet)
    assert state.tls_ext_count == 1


@pytest.mark.parametrize("malformed", ["mf", "ip_length", "tcp_length", "udp_length"])
def test_malformed_or_fragmented_packet_cannot_claim_feature_state(
    tmp_path: Path, malformed: str
) -> None:
    from scapy.all import UDP

    pcap = tmp_path / "malformed.pcap"
    ip = IP(src="10.0.0.1", dst="10.0.0.100")
    transport = TCP(sport=40000, dport=443)
    if malformed == "mf":
        ip.flags = "MF"
    elif malformed == "ip_length":
        ip.len = 300
    elif malformed == "tcp_length":
        transport.dataofs = 15
    else:
        transport = UDP(sport=40000, dport=443, len=200)
    packet = Ether() / ip / transport / Raw(b"x" * 10)
    wrpcap(str(pcap), [packet])
    assert extract_flows(pcap) == {}
