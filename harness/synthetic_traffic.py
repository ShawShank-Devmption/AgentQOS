"""Seeded packet-level synthetic corpus, extracted through the P4-mirror feature path.

A stand-in for the captured P3.4/P3.5/P3.8 corpus. No public dataset carries labeled
autonomous-agent client traffic with per-packet timing, so this generates packet sequences from
explicit workload models and runs them through the shared `ml.extract_features` state updates
and feature-vector construction, which must match features.p4 bit for bit (design.md 4.3, 7.9).
TLS ClientHello bytes are
real records captured from local clients (`harness/client_hellos.json`). Every row is tagged
`source_framework = "synthetic:<profile>"` and must never be reported as the research corpus.
"""

from __future__ import annotations

import argparse
import json
import logging
from bisect import bisect_left, bisect_right
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from common.contracts import FEATURE_CHECKPOINTS, FEATURE_ORDER, SKETCH_EPOCH_US, TrafficClass
from common.feature_math import saturate
from ml.extract_features import FlowState, PacketObservation, feature_vector, update_flow

LOGGER = logging.getLogger(__name__)

CLIENT_HELLOS_PATH = Path(__file__).with_name("client_hellos.json")
MAX_PACKETS = FEATURE_CHECKPOINTS[-1]  # nothing after the last checkpoint reaches the tree
LINK_MBPS = 20  # harness.topology default bottleneck; paces back-to-back segments
TCP_HEADER_BYTES = 66  # Ethernet 14 + IPv4 20 + TCP 20 + timestamp option 12
SYN_BYTES = 74
UDP_HEADER_BYTES = 42
MSS_BYTES = 1448  # 1500 MTU minus IPv4, TCP and timestamp option
SERVER_FLIGHT_BYTES = 4_200  # TLS 1.3 ServerHello..Finished with a typical certificate chain
CLIENT_FINISHED_BYTES = 80
JITTER_MEAN_US = 40  # host scheduling noise added to every arrival
RTP_PAYLOAD = b"\x80\x6f"  # RTP v2 header start: not a TLS record, so the parser fails open

# Workload parameters. SURGE values are from Barford & Crovella, SIGMETRICS 1998 (web
# request generator). LLM step latency and agent fan-out are ASSUMPTIONS to be replaced by
# harness measurements (P3.4).
SURGE_BODY_MU = 9.357  # lognormal object size, median ~11.6 KB
SURGE_BODY_SIGMA = 1.318
SURGE_EMBEDDED_ALPHA = 2.43  # Pareto embedded references per page, k = 1
SURGE_ACTIVE_OFF_SHAPE = 1.46  # Weibull gap between objects of one page
SURGE_ACTIVE_OFF_SCALE_US = 382_000
SURGE_INACTIVE_OFF_ALPHA = 1.5  # Pareto human think time between pages, k = 1 s
SURGE_INACTIVE_OFF_MIN_US = 1_000_000
LLM_STEP_MEDIAN_US = 1_200_000  # ASSUMPTION: one agent reasoning step
LLM_STEP_SIGMA = 0.6
RTT_MEDIAN_US = 20_000  # shared by every profile so RTT never leaks the label
RTT_SIGMA = 0.5
MAX_OBJECT_BYTES = 4_000_000


@dataclass(frozen=True)
class SyntheticFlow:
    """One generated flow with its orchestration-side ground truth (7.20)."""

    flow_id: str
    client_id: str
    start_us: int
    packets: tuple[PacketObservation, ...]
    label: TrafficClass
    profile: str


class _FlowBuilder:
    """Appends one flow's packets as the switch sees them; time keeps running once full."""

    def __init__(self, rng: np.random.Generator, start_us: int, rtt_us: int) -> None:
        self.rng = rng
        self.now = start_us
        self.rtt = rtt_us
        self.packets: list[PacketObservation] = []

    def emit(self, size: int, upstream: bool, payload: bytes = b"") -> None:
        self.now += int(self.rng.exponential(JITTER_MEAN_US))
        if len(self.packets) < MAX_PACKETS:
            self.packets.append(PacketObservation(self.now, size, upstream, payload))

    def segments(self, nbytes: int, upstream: bool, data: bytes = b"") -> None:
        """Send nbytes as back-to-back MSS segments; the receiver ACKs every second one."""
        for index, offset in enumerate(range(0, nbytes, MSS_BYTES), start=1):
            if len(self.packets) >= MAX_PACKETS:
                self.now += _wire_us(nbytes - offset)
                return
            chunk = min(MSS_BYTES, nbytes - offset)
            self.now += _wire_us(TCP_HEADER_BYTES + chunk)
            self.emit(TCP_HEADER_BYTES + chunk, upstream, data[offset : offset + chunk])
            if index % 2 == 0:
                self.emit(TCP_HEADER_BYTES, not upstream)

    def tls_open(self, client_hello: bytes) -> None:
        """TCP handshake, ClientHello split at the MSS, server flight, client Finished."""
        self.emit(SYN_BYTES, True)
        self.now += self.rtt
        self.emit(SYN_BYTES, False)
        self.emit(TCP_HEADER_BYTES, True)
        self.segments(len(client_hello), True, client_hello)
        self.now += self.rtt
        self.segments(SERVER_FLIGHT_BYTES, False)
        self.emit(TCP_HEADER_BYTES + CLIENT_FINISHED_BYTES, True)

    def exchange(self, up_bytes: int, down_bytes: int, server_us: int) -> None:
        """One request/response: request up, wait RTT plus server time, response down."""
        self.segments(up_bytes, True)
        self.now += self.rtt + server_us
        self.segments(down_bytes, False)


@dataclass(frozen=True)
class _Session:
    rng: np.random.Generator
    client_id: str
    start_us: int
    rtt_us: int
    hellos: dict[str, bytes]


GeneratedFlows = list[tuple[int, list[PacketObservation]]]
SessionFn = Callable[[_Session], GeneratedFlows]


def _browse(
    session: _Session, hello: bytes, pages: int, page_gap: Callable[[], int]
) -> GeneratedFlows:
    """Page views of a Chromium client: each page opens connections for its objects."""
    rng, flows, now = session.rng, [], session.start_us
    for _ in range(pages):
        objects = 1 + int(rng.pareto(SURGE_EMBEDDED_ALPHA) + 1)
        connections = min(objects, 1 + int(rng.poisson(1.5)))
        page_end = now
        for conn in range(connections):
            start = now + (0 if conn == 0 else int(rng.uniform(0, 150_000)))
            flow = _FlowBuilder(rng, start, session.rtt_us)
            flow.tls_open(hello)
            for _ in range(conn, objects, connections):
                flow.exchange(int(rng.integers(300, 900)), _surge_size(rng), _server_us(rng))
                flow.now += int(rng.weibull(SURGE_ACTIVE_OFF_SHAPE) * SURGE_ACTIVE_OFF_SCALE_US)
            flows.append((start, flow.packets))
            page_end = max(page_end, flow.now)
        now = page_end + page_gap()
    return flows


def _human_browsing(session: _Session) -> GeneratedFlows:
    pages = int(session.rng.integers(2, 6))
    return _browse(session, session.hellos["chrome"], pages, lambda: _think_us(session.rng))


def _agent_browser(session: _Session) -> GeneratedFlows:
    # Browser Use / Playwright agents drive real Chromium: same ClientHello as a human.
    pages = int(session.rng.integers(3, 10))
    return _browse(session, session.hellos["chrome"], pages, lambda: _llm_us(session.rng))


def _human_video_call(session: _Session) -> GeneratedFlows:
    """Bundled WebRTC UDP flow: 30 fps paced video plus 20 ms Opus audio, both directions."""
    rng, events = session.rng, []
    for upstream in (True, False):
        phase = int(rng.uniform(0, 33_333))
        for frame in range(30):
            frame_us = session.start_us + phase + frame * 33_333
            frame_bytes = int(rng.lognormal(np.log(4_000), 0.5))
            for packet in range(-(-frame_bytes // 1_200)):
                events.append((frame_us + packet * 900, UDP_HEADER_BYTES + 1_200, upstream))
        for tick in range(50):
            audio_us = session.start_us + phase + tick * 20_000
            events.append((audio_us, UDP_HEADER_BYTES + int(rng.integers(90, 160)), upstream))
    events.sort()
    packets = [
        PacketObservation(ts + int(rng.exponential(JITTER_MEAN_US)), size, up, RTP_PAYLOAD)
        for ts, size, up in events[:MAX_PACKETS]
    ]
    packets.sort(key=lambda packet: packet.timestamp_us)
    return [(packets[0].timestamp_us, packets)]


def _agent_mcp(session: _Session) -> GeneratedFlows:
    """Tool-calling loop: one MCP session connection plus short fetch-tool connections."""
    rng = session.rng
    hello = session.hellos[str(rng.choice(["python_httpx_h1", "python_httpx_h2"]))]
    mcp = _FlowBuilder(rng, session.start_us, session.rtt_us)
    mcp.tls_open(hello)
    flows = []
    for _ in range(int(rng.integers(4, 12))):
        mcp.now += _llm_us(rng)
        for _ in range(1 + int(rng.poisson(0.8))):
            up = int(rng.lognormal(np.log(600), 0.6))
            mcp.exchange(up, int(rng.lognormal(np.log(3_000), 1.0)), _server_us(rng))
            if rng.random() < 0.4:
                fetch = _FlowBuilder(rng, mcp.now, session.rtt_us)
                fetch.tls_open(hello)
                fetch.exchange(int(rng.integers(200, 450)), _surge_size(rng), _server_us(rng))
                flows.append((fetch.packets[0].timestamp_us, fetch.packets))
    return [(session.start_us, mcp.packets), *flows]


def _parallel_transfers(
    session: _Session, hello: bytes, up: Callable[[], int], down: Callable[[], int]
) -> GeneratedFlows:
    """Waves of asyncio-style parallel connections issuing back-to-back requests."""
    rng, flows, now = session.rng, [], session.start_us
    for _ in range(int(rng.integers(1, 4))):
        wave_end = now
        for _ in range(int(rng.integers(8, 33))):
            flow = _FlowBuilder(rng, now + int(rng.uniform(0, 20_000)), session.rtt_us)
            start = flow.now
            flow.tls_open(hello)
            for _ in range(int(rng.integers(3, 10))):
                flow.exchange(up(), down(), _server_us(rng))
                flow.now += int(rng.exponential(1_000))  # think_time = 0: client parse only
            flows.append((start, flow.packets))
            wave_end = max(wave_end, flow.now)
        now = wave_end + _llm_us(rng)
    return flows


def _agent_crawler(session: _Session) -> GeneratedFlows:
    rng = session.rng
    return _parallel_transfers(
        session,
        session.hellos["python_httpx_h1"],
        lambda: int(rng.integers(200, 450)),
        lambda: min(int(rng.lognormal(np.log(150_000), 1.0)), MAX_OBJECT_BYTES),
    )


def _agent_ingest(session: _Session) -> GeneratedFlows:
    # Bulk uploads (document batches to a vector store), issued through a curl tool.
    rng = session.rng
    return _parallel_transfers(
        session,
        session.hellos["curl"],
        lambda: min(int(rng.lognormal(np.log(200_000), 1.0)), MAX_OBJECT_BYTES),
        lambda: int(rng.integers(200, 1_500)),
    )


PROFILES: dict[str, tuple[TrafficClass, SessionFn]] = {
    "human_browsing": (TrafficClass.HUMAN_INTERACTIVE, _human_browsing),
    "human_video_call": (TrafficClass.HUMAN_INTERACTIVE, _human_video_call),
    "agent_mcp_tools": (TrafficClass.AGENT_INTERACTIVE, _agent_mcp),
    "agent_browser": (TrafficClass.AGENT_INTERACTIVE, _agent_browser),
    "agent_crawler": (TrafficClass.AGENT_BULK, _agent_crawler),
    "agent_ingest": (TrafficClass.AGENT_BULK, _agent_ingest),
}


def load_client_hellos(path: Path = CLIENT_HELLOS_PATH) -> dict[str, bytes]:
    """Return the captured ClientHello records keyed by client name.

    Raises:
        ValueError: If the fixture is malformed.
    """
    try:
        entries = json.loads(path.read_text())["client_hellos"]
        return {name: bytes.fromhex(entry["hex"]) for name, entry in entries.items()}
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"malformed ClientHello fixture {path}: {exc}") from exc


def generate_flows(seed: int, clients_per_profile: int) -> list[SyntheticFlow]:
    """Generate every profile's client sessions; deterministic for a given seed."""
    hellos = load_client_hellos()
    flows = []
    for profile_index, (profile, (label, session_fn)) in enumerate(PROFILES.items()):
        rng = np.random.default_rng([seed, profile_index])
        for client in range(clients_per_profile):
            client_id = f"{profile}-{client}"
            rtt_us = int(rng.lognormal(np.log(RTT_MEDIAN_US), RTT_SIGMA))
            start_us = int(rng.uniform(0, SKETCH_EPOCH_US))  # client phase vs sketch epochs
            session = _Session(rng, client_id, start_us, rtt_us, hellos)
            for index, (flow_start, packets) in enumerate(session_fn(session)):
                flow_id = f"syn-{client_id}-{index}"
                flows.append(
                    SyntheticFlow(flow_id, client_id, flow_start, tuple(packets), label, profile)
                )
    return flows


def fanout_estimates(
    flows: Sequence[SyntheticFlow], checkpoint: int = FEATURE_CHECKPOINTS[0]
) -> dict[str, int]:
    """Estimate each client's active-epoch claims at the requested packet checkpoint.

    Epochs start at the first captured packet, matching offline extraction. This synthetic
    estimate models exact per-client counts, without hash-slot or sketch collisions.
    """
    if checkpoint not in FEATURE_CHECKPOINTS:
        raise ValueError(f"checkpoint must be in {FEATURE_CHECKPOINTS}")
    if not flows:
        return {}
    origin_us = min(flow.packets[0].timestamp_us for flow in flows)
    starts_by_client: dict[str, list[int]] = {}
    for flow in flows:
        starts_by_client.setdefault(flow.client_id, []).append(flow.packets[0].timestamp_us)
    for starts in starts_by_client.values():
        starts.sort()
    estimates = {}
    for flow in flows:
        read_us = flow.packets[min(len(flow.packets), checkpoint) - 1].timestamp_us
        # §7.9/7.10: read the current epoch at this checkpoint, never a cached earlier value.
        epoch_start = origin_us + (read_us - origin_us) // SKETCH_EPOCH_US * SKETCH_EPOCH_US
        starts = starts_by_client[flow.client_id]
        claims = bisect_right(starts, read_us) - bisect_left(starts, epoch_start)
        estimates[flow.flow_id] = saturate(claims, 16)
    return estimates


def corpus_frame(flows: Sequence[SyntheticFlow]) -> pd.DataFrame:
    """Return the `ml.train` feature table: one row per flow per reached checkpoint."""
    fanout = {checkpoint: fanout_estimates(flows, checkpoint) for checkpoint in FEATURE_CHECKPOINTS}
    rows = []
    for flow in flows:
        state = FlowState()
        for checkpoint, packet in enumerate(flow.packets, start=1):
            update_flow(state, packet)
            if checkpoint not in FEATURE_CHECKPOINTS:
                continue
            vector = feature_vector(state, fanout[checkpoint][flow.flow_id])
            row = {"flow_id": flow.flow_id, "checkpoint": checkpoint}
            row.update(zip(FEATURE_ORDER, vector, strict=True))
            row["label"] = int(flow.label)
            row["source_framework"] = f"synthetic:{flow.profile}"
            rows.append(row)
    return pd.DataFrame(rows)


def main(argv: Sequence[str] | None = None) -> int:
    """Write the synthetic feature table as CSV for `python -m ml.train --features`.

    Returns:
        Zero on success, one if the ClientHello fixture or output path is unusable.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--clients-per-profile", type=int, default=40)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        frame = corpus_frame(generate_flows(args.seed, args.clients_per_profile))
        args.out.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(args.out, index=False)
    except (OSError, ValueError) as exc:
        LOGGER.error("synthetic corpus failed: %s", exc)
        return 1
    LOGGER.info(
        "wrote %d rows from %d flows to %s", len(frame), frame["flow_id"].nunique(), args.out
    )
    return 0


def _wire_us(frame_bytes: int) -> int:
    return frame_bytes * 8 // LINK_MBPS


def _surge_size(rng: np.random.Generator) -> int:
    return min(int(rng.lognormal(SURGE_BODY_MU, SURGE_BODY_SIGMA)), MAX_OBJECT_BYTES)


def _server_us(rng: np.random.Generator) -> int:
    return int(rng.lognormal(np.log(30_000), 0.8))


def _think_us(rng: np.random.Generator) -> int:
    return int((rng.pareto(SURGE_INACTIVE_OFF_ALPHA) + 1) * SURGE_INACTIVE_OFF_MIN_US)


def _llm_us(rng: np.random.Generator) -> int:
    return int(rng.lognormal(np.log(LLM_STEP_MEDIAN_US), LLM_STEP_SIGMA))


if __name__ == "__main__":
    raise SystemExit(main())
