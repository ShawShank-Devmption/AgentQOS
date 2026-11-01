"""Corpus build: background-service flows leave the training rows, never the replay."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest
from scapy.all import DNS, DNSQR, DNSRR, IP, TCP, UDP, Ether, Raw, wrpcap

from common.contracts import LABEL_FIELDS
from ml import build_corpus
from tests.test_ja4 import client_hello

DEVICE = "10.42.0.12"
RESOLVER = "1.1.1.1"
RULES = Path("eval/configs/corpus_noise.json")


def _hello(name: str) -> bytes:
    body = name.encode()
    entry = b"\x00" + len(body).to_bytes(2, "big") + body
    return client_hello(extensions=((0, len(entry).to_bytes(2, "big") + entry),))


def _ether() -> Ether:
    return Ether(src="02:00:00:00:00:01", dst="02:00:00:00:00:02")


def _tcp(dst: str, sport: int, payloads: list[bytes]) -> list[Ether]:
    up = _ether() / IP(src=DEVICE, dst=dst)
    down = _ether() / IP(src=dst, dst=DEVICE)
    packets = [up / TCP(sport=sport, dport=443, flags="S", seq=0)]
    packets.append(down / TCP(sport=443, dport=sport, flags="SA", seq=0, ack=1))
    seq = 1
    for payload in payloads:
        packets.append(up / TCP(sport=sport, dport=443, flags="PA", seq=seq) / Raw(payload))
        seq += len(payload)
    return packets


def _udp(dst: str, sport: int, dport: int, count: int) -> list[Ether]:
    return [
        _ether() / IP(src=DEVICE, dst=dst) / UDP(sport=sport, dport=dport) / Raw(b"x" * 40)
        for _ in range(count)
    ]


def _dns(sport: int, name: str, address: str) -> list[Ether]:
    query = DNS(id=sport, rd=1, qd=DNSQR(qname=name))
    answer = DNS(id=sport, qr=1, qd=DNSQR(qname=name), an=DNSRR(rrname=name, rdata=address))
    return [
        _ether() / IP(src=DEVICE, dst=RESOLVER) / UDP(sport=sport, dport=53) / query,
        _ether() / IP(src=RESOLVER, dst=DEVICE) / UDP(sport=53, dport=sport) / answer,
    ]


def _session(corpus: Path, name: str, status: str, flows: dict[str, list[Ether]]) -> Path:
    session = corpus / name
    session.mkdir(parents=True)
    packets = [packet for flow in flows.values() for packet in flow]
    for index, packet in enumerate(packets):
        packet.time = 1_700_000_000 + index * 0.01
    wrpcap(str(session / "capture.pcap"), packets)
    with (session / "labels.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=LABEL_FIELDS)
        writer.writeheader()
        for flow_id, flow in flows.items():
            ip = flow[0][IP]
            writer.writerow(
                {
                    "flow_id": flow_id,
                    "src_ip": ip.src,
                    "dst_ip": ip.dst,
                    "proto": ip.proto,
                    "src_port": ip.payload.sport,
                    "dst_port": ip.payload.dport,
                    "label": 2,
                    "source_framework": "codex-cli-interactive",
                    "pcap_file": "capture.pcap",
                }
            )
    (session / "session.json").write_text(json.dumps({"status": status}))
    return session


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    corpus = tmp_path / "corpus"
    hello = _hello("api.docker.com")
    flows = {
        "agent": _tcp("172.64.155.209", 50001, [_hello("chatgpt.com"), *[b"y" * 300] * 6]),
        "icloud": _tcp("17.248.239.10", 50002, [_hello("p173-contacts.icloud.com")]),
        # A post-quantum-sized ClientHello arrives in two segments; the name must still be found.
        "docker": _tcp("54.163.166.73", 50003, [hello[:40], hello[40:]]),
        "ntp": _udp("162.159.200.1", 50004, 123, 2),
        "dns-calendar": _dns(50005, "calendar.google.com.", "142.250.0.10")[:1],
        "quic-calendar": _udp("142.250.0.10", 50006, 443, 3),
        "dns-api": _dns(50007, "www.googleapis.com.", "142.250.0.20")[:1],
        "quic-shared": _udp("142.250.0.20", 50008, 443, 3),
    }
    # DNS answers come back on the query flows; 142.250.0.20 also serves calendar.google.com.
    flows["dns-calendar"] += _dns(50005, "calendar.google.com.", "142.250.0.10")[1:]
    flows["dns-api"] += _dns(50007, "www.googleapis.com.", "142.250.0.20")[1:]
    flows["dns-api"] += _dns(50007, "calendar.google.com.", "142.250.0.20")[1:]
    _session(corpus, "s1", "complete", flows)
    _session(corpus, "s2", "failed", {"agent": _tcp("172.64.155.209", 50009, [b"z"])})
    return corpus


def _noise(output: Path) -> dict[str, str]:
    with (output / "noise.csv").open() as handle:
        return {row["flow_id"]: row["reason"] for row in csv.DictReader(handle)}


def test_background_flows_leave_training_and_shared_addresses_stay(
    corpus: Path, tmp_path: Path
) -> None:
    output = tmp_path / "build"
    recorded = hashlib.sha256((corpus / "s1" / "labels.csv").read_bytes()).hexdigest()
    assert build_corpus.main([str(corpus), "--rules", str(RULES), "--output", str(output)]) == 0
    noise = _noise(output)
    assert set(noise) == {"s1:icloud", "s1:docker", "s1:ntp", "s1:dns-calendar", "s1:quic-calendar"}
    assert noise["s1:icloud"] == "iCloud (p173-contacts.icloud.com)"
    assert noise["s1:docker"] == "Docker Desktop (api.docker.com)"
    assert noise["s1:ntp"] == "time sync (NTP)"
    assert noise["s1:dns-calendar"] == "Google Calendar (DNS lookup of calendar.google.com)"
    assert noise["s1:quic-calendar"] == "Google Calendar (calendar.google.com)"
    kept = pd.read_csv(output / "sessions" / "s1" / "labels.csv", dtype=str)
    assert list(kept.columns) == list(LABEL_FIELDS)
    assert set(kept["flow_id"]) == {"agent", "dns-api", "quic-shared"}
    features = pd.read_csv(output / "features.csv")
    assert set(features["flow_id"]) == {"s1:agent"}
    groups = pd.read_csv(output / "groups.csv")
    assert list(groups.columns) == ["flow_id", "group_id"]
    assert dict(zip(groups["flow_id"], groups["group_id"], strict=True)) == {"s1:agent": "s1"}
    summary = json.loads((output / "summary.json").read_text())
    assert summary["skipped"] == {"s2": "failed"}
    assert summary["sessions"]["s1"]["noise_flows"] == 5
    assert summary["sessions"]["s1"]["training_flows"] == 3
    assert summary["rules_sha256"] == hashlib.sha256(RULES.read_bytes()).hexdigest()
    # Sessions stay exactly as recorded.
    assert hashlib.sha256((corpus / "s1" / "labels.csv").read_bytes()).hexdigest() == recorded


def test_existing_output_is_never_replaced(corpus: Path, tmp_path: Path) -> None:
    output = tmp_path / "build"
    output.mkdir()
    assert build_corpus.main([str(corpus), "--rules", str(RULES), "--output", str(output)]) == 1
    assert not any(output.iterdir())


@pytest.mark.parametrize(
    "rules",
    [
        {"ports": []},
        {"ports": [{"proto": 1, "port": 1, "reason": "x"}], "domains": []},
        {"ports": [], "domains": [{"suffix": "", "reason": "x"}]},
        {"ports": [], "domains": [{"suffix": "Apple.com", "reason": "x"}]},
    ],
)
def test_malformed_rules_are_rejected(tmp_path: Path, rules: dict[str, object]) -> None:
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(rules))
    with pytest.raises(build_corpus.CorpusError):
        build_corpus.load_rules(path)
