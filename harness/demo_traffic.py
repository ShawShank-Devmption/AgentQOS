"""D2 application cadence models for measured UDP schedules, not real TCP sessions."""

from __future__ import annotations

import math
import random
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from harness.traffic import HEADER, WIRE_BYTES

SHAPE_NAMES = ("human_video_call", "agent_mcp", "agent_ingest", "agent_crawler")
VIDEO_FIELDS = {
    "frame_interval_s",
    "frame_bytes",
    "video_payload_bytes",
    "video_packet_gap_s",
    "audio_interval_s",
    "audio_bytes",
    "jitter_mean_s",
}
TRANSFER_FIELDS = {
    "segment_payload_bytes",
    "segment_gap_s",
    "max_object_bytes",
    "server_delay_s",
    "jitter_mean_s",
    "start_spread_s",
    "request_bytes",
    "response_bytes",
}
MCP_FIELDS = TRANSFER_FIELDS | {"think_s", "calls_per_step_mean"}
BULK_FIELDS = TRANSFER_FIELDS | {
    "requests_per_flow",
    "parallel_flows",
    "wave_gap_s",
    "parse_gap_s",
}


def _number(value: object, name: str, *, zero: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"shape {name} must be a finite number")
    if not math.isfinite(value) or value < 0 or (value == 0 and not zero):
        raise ValueError(f"shape {name} must be {'nonnegative' if zero else 'positive'}")
    return float(value)


def _range(value: object, name: str) -> None:
    if (
        not isinstance(value, list)
        or len(value) != 2
        or any(type(item) is not int or item <= 0 for item in value)
        or value[0] > value[1]
    ):
        raise ValueError(f"shape {name} must be an ordered positive integer pair")


def _distribution(value: object) -> None:
    if not isinstance(value, dict):
        raise ValueError("shape distribution must be an object")
    if value.get("distribution") == "lognormal":
        if set(value) != {"distribution", "median", "sigma"}:
            raise ValueError("invalid lognormal shape distribution fields")
        _number(value["median"], "distribution median")
        _number(value["sigma"], "distribution sigma", zero=True)
    elif value.get("distribution") == "uniform":
        if set(value) != {"distribution", "min", "max"}:
            raise ValueError("invalid uniform shape distribution fields")
        _number(value["min"], "distribution min")
        _number(value["max"], "distribution max")
        if value["min"] > value["max"]:
            raise ValueError("shape distribution bounds are reversed")
    else:
        raise ValueError("unknown shape distribution")


def validate_shapes(shapes: object) -> None:
    """Reject malformed shape parameters before any network resources are allocated."""
    if not isinstance(shapes, dict) or set(shapes) != set(SHAPE_NAMES):
        raise ValueError("traffic_shapes must define the video, MCP, ingest and crawler shapes")
    distributions = {
        "frame_bytes",
        "audio_bytes",
        "request_bytes",
        "response_bytes",
        "server_delay_s",
        "think_s",
        "wave_gap_s",
    }
    for name, shape in shapes.items():
        expected = (
            VIDEO_FIELDS
            if name == "human_video_call"
            else (MCP_FIELDS if name == "agent_mcp" else BULK_FIELDS)
        )
        if not isinstance(shape, dict) or set(shape) != expected:
            raise ValueError(f"invalid shape fields for {name}")
        for key, value in shape.items():
            if key in distributions:
                _distribution(value)
            elif key in ("requests_per_flow", "parallel_flows"):
                _range(value, key)
            else:
                _number(value, key, zero=key in ("jitter_mean_s", "calls_per_step_mean"))
            if key == "max_object_bytes" and type(value) is not int:
                raise ValueError("shape max_object_bytes must be a positive integer")
            if key in ("video_payload_bytes", "segment_payload_bytes") and (
                type(value) is not int or not HEADER.size <= value <= 1472
            ):
                raise ValueError(f"shape {key} must fit the measurement header and UDP MTU")
        if "audio_bytes" in shape:
            audio = shape["audio_bytes"]
            if (
                audio["distribution"] != "uniform"
                or not HEADER.size <= audio["min"] <= (audio["max"]) <= 1472
            ):
                raise ValueError("shape audio payload must fit the measurement header and UDP MTU")


def _sample(spec: dict[str, Any], rng: random.Random) -> float:
    if spec["distribution"] == "uniform":
        if type(spec["min"]) is int and type(spec["max"]) is int:
            return float(rng.randint(spec["min"], spec["max"]))
        return rng.uniform(spec["min"], spec["max"])
    try:
        value = rng.lognormvariate(math.log(spec["median"]), spec["sigma"])
    except OverflowError as exc:
        raise ValueError("shape distribution overflow") from exc
    if not math.isfinite(value) or value <= 0:
        raise ValueError("shape distribution produced a nonpositive or nonfinite sample")
    return value


@dataclass(frozen=True)
class ScheduledPacket:
    """One scheduled datagram, before its identity is assigned by the experiment planner."""

    offset_s: float
    payload_bytes: int


@dataclass
class _Flow:
    shape: dict[str, Any]
    rng: random.Random
    horizon: float
    limit: int
    now: float
    packets: list[ScheduledPacket] = field(default_factory=list)

    def emit(self, size: int) -> None:
        if self.shape["jitter_mean_s"]:
            self.now += self.rng.expovariate(1 / self.shape["jitter_mean_s"])
        if self.now < self.horizon:
            if len(self.packets) >= self.limit:
                raise ValueError("planned packet count exceeds profile max_packets")
            self.packets.append(ScheduledPacket(self.now, max(HEADER.size, size)))

    def segments(self, size: int) -> None:
        segment = self.shape["segment_payload_bytes"]
        for offset in range(0, size, segment):
            self.now += self.shape["segment_gap_s"]
            if self.now >= self.horizon:
                return
            self.emit(min(segment, size - offset))

    def exchange(self) -> None:
        for direction in ("request_bytes", "response_bytes"):
            size = min(
                int(_sample(self.shape[direction], self.rng)), self.shape["max_object_bytes"]
            )
            self.segments(size)
            if direction == "request_bytes":
                self.now += _sample(self.shape["server_delay_s"], self.rng)


def _video(
    shape: dict[str, Any], horizon: float, rng: random.Random, limit: int
) -> list[ScheduledPacket]:
    flow = _Flow(shape, rng, horizon, limit, 0)
    phase = rng.random() * min(shape["frame_interval_s"], horizon)
    frame = phase
    while frame < horizon:
        frame_bytes = int(_sample(shape["frame_bytes"], rng))
        payload = shape["video_payload_bytes"]
        for index in range(math.ceil(frame_bytes / payload)):
            flow.now = frame + index * shape["video_packet_gap_s"]
            if flow.now >= horizon:
                break
            flow.emit(payload)
        frame += shape["frame_interval_s"]
    tick = phase
    while tick < horizon:
        flow.now = tick
        flow.emit(int(_sample(shape["audio_bytes"], rng)))
        tick += shape["audio_interval_s"]
    return sorted(flow.packets, key=lambda packet: packet.offset_s)


def _mcp(
    shape: dict[str, Any], horizon: float, rng: random.Random, limit: int
) -> list[ScheduledPacket]:
    flow = _Flow(shape, rng, horizon, limit, rng.random() * min(shape["start_spread_s"], horizon))
    while flow.now < horizon:
        # One tool call plus Poisson(lambda) additional calls, as in _agent_mcp.
        step = 0.0
        flow.exchange()
        while (
            flow.now < horizon
            and (step := step + rng.expovariate(1)) < shape["calls_per_step_mean"]
        ):
            flow.exchange()
        flow.now += _sample(shape["think_s"], rng)
    return flow.packets


def lane_schedules(
    name: str,
    shape: dict[str, Any],
    horizon: float,
    rng: random.Random,
    limit: int,
    *,
    wire_budget: int | None = None,
) -> Iterator[list[ScheduledPacket]]:
    """Yield a video/MCP session or waves of bulk connections at their configured cadence."""
    if name == "human_video_call":
        yield _video(shape, horizon, rng, limit)
    elif name == "agent_mcp":
        yield _mcp(shape, horizon, rng, limit)
    else:
        now = 0.0
        allocated = 0
        while now < horizon:
            wave_end = now
            wave = []
            for _ in range(rng.randint(*shape["parallel_flows"])):
                flow = _Flow(
                    shape, rng, horizon, limit, now + rng.random() * shape["start_spread_s"]
                )
                for _ in range(rng.randint(*shape["requests_per_flow"])):
                    flow.exchange()
                    flow.now += rng.expovariate(1 / shape["parse_gap_s"])
                    if flow.now >= horizon:
                        break
                wave_end = max(wave_end, flow.now)
                if flow.packets:
                    wave.append(flow.packets)
            next_wave = wave_end + _sample(shape["wave_gap_s"], rng)
            if wire_budget is None:
                yield from wave
            elif wave:
                # Reserve the original wave interval, even when its connections are truncated.
                # Otherwise low-share traffic spends the entire run budget in the first wave.
                cumulative = int(wire_budget * min(next_wave, horizon) / horizon)
                per_flow = (cumulative - allocated) // len(wave)
                allocated = cumulative
                for packets in wave:
                    selected = take_packet_budget(packets, per_flow)
                    if selected:
                        yield selected
            now = next_wave


def take_packet_budget(packets: list[ScheduledPacket], budget: int) -> list[ScheduledPacket]:
    """Take a connection prefix, shortening its final datagram to fit a frame-byte budget."""
    selected = []
    for packet in packets:
        if budget < HEADER.size + WIRE_BYTES:
            break
        size = min(packet.payload_bytes, budget - WIRE_BYTES)
        selected.append(ScheduledPacket(packet.offset_s, size))
        budget -= size + WIRE_BYTES
    return selected
