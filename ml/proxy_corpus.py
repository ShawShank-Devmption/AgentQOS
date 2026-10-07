"""Proxy training corpus until the harness corpus exists (P3.8). NOT evidence for the paper.

Sources:
  * Labayen, Magana, Morato, Izal, "Network traffic and code for machine learning classification",
    Mendeley Data v3, doi:10.17632/5pmnkshffm.3, CC BY 4.0. A single real user performing
    labelled activities; per-packet CSVs of payload-carrying TCP/UDP packets.
  * A seeded synthetic model of MCP-style agent tool calls (no public source exists): parallel
    request/response flows with zero think time. Its separability is by construction.

Labels come from the activity each trace was captured for (section 7.20), never from features.
Every packet goes through the same fixed-point mirror as the switch (ml/extract_features.py).
"""

from __future__ import annotations

import argparse
import ipaddress
import logging
from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pandas as pd

from common.contracts import (
    FEATURE_CHECKPOINTS,
    FEATURE_ORDER,
    SKETCH_EPOCH_US,
    TrafficClass,
)
from ml.extract_features import FlowState, PacketObservation, feature_vector, update_flow

LOGGER = logging.getLogger(__name__)

AGENT_SOURCE = "synthetic:agent-tool-calls"
# Mendeley activity -> class. Idle is OS background traffic, not a user activity: excluded.
ACTIVITY_LABELS = {
    "web": TrafficClass.HUMAN_INTERACTIVE,
    "interactive": TrafficClass.HUMAN_INTERACTIVE,
    "video": TrafficClass.HUMAN_INTERACTIVE,
    "bulk": TrafficClass.AGENT_BULK,  # proxy for transport shape only
}
# The CSVs hold payload lengths; frame = payload + Ethernet/IPv4/L4 headers (TCP w/ timestamps).
HEADER_BYTES = {6: 66, 17: 42}
SERVICE_PORTS = frozenset({53, 67, 68, 123, 137, 138, 1900, 5353})
_MAX_PACKETS = max(FEATURE_CHECKPOINTS)
FlowTable = dict[str, list[PacketObservation]]


def read_trace_flows(csv_path: Path, host: str) -> tuple[FlowTable, list[int]]:
    """Group one trace into bidirectional flows with the user host as client.

    Returns:
        Labelled flows (service/multicast traffic removed, first 64 packets each) and the sorted
        claim times of every flow, which still count toward the host's fan-out sketch.
    """
    frame = pd.read_csv(csv_path)
    frame = frame[frame["proto"].isin(HEADER_BYTES)].sort_values("time", kind="stable")
    upstream = frame["ip_src"] == host
    frame = frame.assign(
        upstream=upstream,
        server_ip=frame["ip_dst"].where(upstream, frame["ip_src"]),
        client_port=frame["src_port"].where(upstream, frame["dst_port"]),
        server_port=frame["dst_port"].where(upstream, frame["src_port"]),
        timestamp_us=(frame["time"] * 1_000_000).round().astype("int64"),
    )
    keys = ["server_ip", "proto", "client_port", "server_port"]
    claims = sorted(frame.groupby(keys)["timestamp_us"].min().tolist())
    flows: FlowTable = {}
    for (server_ip, proto, client_port, server_port), packets in frame.groupby(keys, sort=False):
        if server_port in SERVICE_PORTS or client_port in SERVICE_PORTS:
            continue
        if _is_group(server_ip):
            continue
        flow_id = f"{csv_path.name}:{host}:{client_port}->{server_ip}:{server_port}/{proto}"
        flows[flow_id] = [
            PacketObservation(int(ts), int(size) + HEADER_BYTES[proto], bool(up))
            for ts, size, up in packets[["timestamp_us", "data_len", "upstream"]]
            .head(_MAX_PACKETS)
            .itertuples(index=False)
        ]
    return flows, claims


def fanout_at(claims: Sequence[int], epoch_origin_us: int, now_us: int) -> int:
    """Flows the host claimed in the current sketch epoch up to `now_us` (FR-3, 6.4)."""
    epoch_start = now_us - (now_us - epoch_origin_us) % SKETCH_EPOCH_US
    return bisect_right(claims, now_us) - bisect_left(claims, epoch_start)


def synthetic_agent_flows(sessions: int, seed: int) -> tuple[FlowTable, dict[str, list[int]]]:
    """Generate MCP-style tool-call sessions: N parallel flows, back-to-back request/response.

    Returns the flows and, per flow, its session host's sorted claim times (its fan-out).

    Model (assumptions, not measurements): 4-32 parallel flows per session started within 5 ms;
    4-40 exchanges per flow; JSON-RPC request 300-1500 B; 1-3 response chunks of 200-1400 B
    spaced 0.2-2 ms; path RTT lognormal (median 30 ms); tool time lognormal (median 40 ms);
    1-5 ms client overhead before the next request; zero human think time.
    """
    rng = np.random.default_rng(seed)
    flows: FlowTable = {}
    claims_by_flow: dict[str, list[int]] = {}
    for session in range(sessions):
        host = f"10.66.{session // 250}.{session % 250 + 1}"
        session_start = int(rng.integers(0, 3_600_000_000))
        rtt = int(rng.lognormal(np.log(30_000), 0.5))
        claims = []
        for flow in range(int(rng.integers(4, 33))):
            now = session_start + int(rng.integers(0, 5_000))
            claims.append(now)
            packets = []
            for _ in range(int(rng.integers(4, 41))):
                packets.append(PacketObservation(now, int(rng.integers(300, 1501)) + 66, True))
                now += rtt + int(rng.lognormal(np.log(40_000), 0.8))
                for _ in range(int(rng.integers(1, 4))):
                    packets.append(PacketObservation(now, int(rng.integers(200, 1401)) + 66, False))
                    now += int(rng.integers(200, 2_001))
                now += int(rng.integers(1_000, 5_001))
            flow_id = f"{AGENT_SOURCE}:{host}:{40000 + flow}->10.66.255.1:443/6"
            flows[flow_id] = packets[:_MAX_PACKETS]
            claims_by_flow[flow_id] = claims  # one shared list per session, sorted below
        claims.sort()
    return flows, claims_by_flow


def build_feature_frame(root: Path, agent_sessions: int, seed: int) -> pd.DataFrame:
    """Build the train.py feature table from the extracted dataset under `root`.

    Raises:
        FileNotFoundError: If `data/raw/mapping.csv` is missing.
    """
    raw = root / "data" / "raw"
    mapping = pd.read_csv(raw / "mapping.csv")
    rows: list[dict] = []
    for host, file_name, activity in mapping.itertuples(index=False):
        if activity not in ACTIVITY_LABELS:
            continue
        flows, claims = read_trace_flows(raw / "csv" / file_name, host)
        origin = claims[0] if claims else 0
        for flow_id, packets in flows.items():
            rows += _checkpoint_rows(
                flow_id, packets, claims, origin, ACTIVITY_LABELS[activity], f"mendeley:{activity}"
            )
    agent_flows, claims_by_flow = synthetic_agent_flows(agent_sessions, seed)
    for flow_id, packets in agent_flows.items():
        claims = claims_by_flow[flow_id]
        rows += _checkpoint_rows(
            flow_id, packets, claims, claims[0], TrafficClass.AGENT_INTERACTIVE, AGENT_SOURCE
        )
    columns = ["flow_id", "checkpoint", *FEATURE_ORDER, "label", "source_framework"]
    return pd.DataFrame(rows, columns=columns)


def main(argv: Sequence[str] | None = None) -> int:
    """Write the proxy feature table as CSV.

    Returns:
        Zero on success, one if the dataset is missing or malformed.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="extracted dataset directory")
    parser.add_argument("--agent-sessions", type=int, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        frame = build_feature_frame(args.root, args.agent_sessions, args.seed)
    except (OSError, KeyError, ValueError) as exc:
        LOGGER.error("could not build proxy corpus: %s", exc)
        return 1
    frame.to_csv(args.out, index=False)
    counts = frame.groupby("source_framework")["flow_id"].nunique().to_dict()
    LOGGER.info("wrote %d rows; flows per source: %s", len(frame), counts)
    return 0


def _checkpoint_rows(
    flow_id: str,
    packets: list[PacketObservation],
    claims: Sequence[int],
    origin: int,
    label: TrafficClass,
    source: str,
) -> list[dict]:
    state = FlowState()
    rows = []
    for count, packet in enumerate(packets, start=1):
        update_flow(state, packet)
        if count in FEATURE_CHECKPOINTS:
            fanout = fanout_at(claims, origin, packet.timestamp_us)
            vector = feature_vector(state, fanout)
            rows.append(
                {
                    "flow_id": flow_id,
                    "checkpoint": count,
                    **dict(zip(FEATURE_ORDER, vector, strict=True)),
                    "label": int(label),
                    "source_framework": source,
                }
            )
    return rows


def _is_group(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return ip.is_multicast or address == "255.255.255.255"


if __name__ == "__main__":
    raise SystemExit(main())
