"""Proxy corpus: Mendeley 5pmnkshffm traces + synthetic agent tool-call flows -> feature table."""

from pathlib import Path

import pytest

from common.contracts import TrafficClass
from ml.proxy_corpus import (
    AGENT_SOURCE,
    build_feature_frame,
    fanout_at,
    read_trace_flows,
    synthetic_agent_flows,
)
from ml.train import validate_feature_frame

HOST = "192.168.1.149"
HEADER = "time,proto,data_len,ip_src,ip_dst,src_port,dst_port\n"


def _trace(path: Path, rows: list[str]) -> Path:
    path.write_text(HEADER + "".join(f"{row}\n" for row in rows))
    return path


def _flow_rows(start: float, count: int, gap: float, port: int, size: int = 500) -> list[str]:
    rows = []
    for i in range(count):
        t = start + i * gap
        if i % 2 == 0:
            rows.append(f"{t:.6f},6,{size},{HOST},93.184.216.34,{port},443")
        else:
            rows.append(f"{t:.6f},6,{size * 2},93.184.216.34,{HOST},443,{port}")
    return rows


def test_read_trace_groups_bidirectional_flows_with_host_as_client(tmp_path: Path) -> None:
    rows = _flow_rows(100.0, 4, 0.01, 40000) + [
        f"100.5,17,40,{HOST},192.168.1.1,5555,53",  # DNS: service traffic, not labelled
        f"100.6,17,180,{HOST},239.255.255.250,5556,1900",  # SSDP multicast
    ]
    flows, claims = read_trace_flows(_trace(tmp_path / "web_1page_01.csv", rows), HOST)
    assert list(flows) == [f"web_1page_01.csv:{HOST}:40000->93.184.216.34:443/6"]
    packets = next(iter(flows.values()))
    assert [p.upstream for p in packets] == [True, False, True, False]
    assert [p.size_bytes for p in packets] == [566, 1066, 566, 1066]  # payload + 66 B headers
    assert packets[1].timestamp_us - packets[0].timestamp_us == 10_000
    assert len(claims) == 3  # service flows still claim switch slots (sketch counts them)


def test_fanout_counts_claims_in_the_current_one_second_epoch() -> None:
    claims = [0, 200_000, 900_000, 1_500_000]
    assert fanout_at(claims, epoch_origin_us=0, now_us=950_000) == 3
    assert fanout_at(claims, epoch_origin_us=0, now_us=1_600_000) == 1
    assert fanout_at(claims, epoch_origin_us=0, now_us=100_000) == 1


def test_synthetic_agent_flows_are_seeded_machine_paced_fanouts() -> None:
    first = synthetic_agent_flows(sessions=3, seed=9)
    assert first == synthetic_agent_flows(sessions=3, seed=9)
    assert first != synthetic_agent_flows(sessions=3, seed=10)
    flows, claims_by_flow = first
    assert len(flows) >= 3 * 4  # at least four parallel tool-call flows per session
    assert all(len(packets) >= 8 for packets in flows.values())
    assert set(claims_by_flow) == set(flows)
    assert all(len(claims) >= 4 for claims in claims_by_flow.values())


def test_build_feature_frame_labels_by_activity_and_respects_contract(tmp_path: Path) -> None:
    raw = tmp_path / "data" / "raw"
    (raw / "csv").mkdir(parents=True)
    _trace(raw / "csv" / "web_1page_01.csv", _flow_rows(10.0, 20, 0.3, 40001))
    _trace(raw / "csv" / "bulk_xs_01.csv", _flow_rows(10.0, 70, 0.0005, 40002, size=1448))
    _trace(raw / "csv" / "idle_none_01.csv", _flow_rows(10.0, 20, 1.0, 40003))
    (raw / "mapping.csv").write_text(
        "host,file,label\n"
        f"{HOST},web_1page_01.csv,web\n{HOST},bulk_xs_01.csv,bulk\n{HOST},idle_none_01.csv,idle\n"
    )
    frame = build_feature_frame(tmp_path, agent_sessions=2, seed=1)
    validate_feature_frame(frame)
    by_source = frame.groupby("source_framework")["label"].unique()
    assert list(by_source["mendeley:web"]) == [TrafficClass.HUMAN_INTERACTIVE]
    assert list(by_source["mendeley:bulk"]) == [TrafficClass.AGENT_BULK]
    assert list(by_source[AGENT_SOURCE]) == [TrafficClass.AGENT_INTERACTIVE]
    assert "mendeley:idle" not in by_source  # background OS traffic is not a user activity
    web = frame[frame["source_framework"] == "mendeley:web"]
    assert sorted(web["checkpoint"]) == [6, 16]
    assert web["tls_ext_count"].eq(0).all()  # CSVs carry no payload bytes


def test_missing_mapping_fails_fast(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        build_feature_frame(tmp_path, agent_sessions=1, seed=1)
