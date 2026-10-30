"""Built-in measured UDP workload for the frozen experiment cell schema."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import random
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from common.contracts import (
    BURST_INTENSITIES,
    CLASS_TREATMENTS,
    FEATURE_CHECKPOINTS,
    TRAINING_LABELS,
    TrafficClass,
)
from harness.demo_traffic import lane_schedules, take_packet_budget, validate_shapes
from harness.topology import HOSTS
from harness.traffic import HEADER, WIRE_BYTES, payload_sizes

LOGGER = logging.getLogger(__name__)
PROFILE_FIELDS = {
    "provenance",
    "destination_port",
    "human_flows",
    "agent_interactive_flows",
    "agent_bulk_flows",
    "payload_bytes",
    "drain_s",
    "offered_load_multiplier",
    "burst_parallelism",
    "agent_call_s",
    "socket_buffer_bytes",
    "poll_s",
    "max_packets",
    "diffserv_rules",
    "traffic_shapes",
}


def _positive_int(value: object, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _positive_float(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a positive finite number")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return result


def load_profile(path: Path) -> dict[str, Any]:
    """Validate the bounded synthetic workload profile before network allocation."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or set(raw) not in (
        PROFILE_FIELDS,
        PROFILE_FIELDS - {"traffic_shapes"},
    ):
        raise ValueError("workload profile fields differ from the expected schema")
    if raw["provenance"] != "synthetic_udp_stress":
        raise ValueError("workload provenance must identify synthetic UDP stress")
    if "traffic_shapes" in raw:
        validate_shapes(raw["traffic_shapes"])
    for name in (
        "destination_port",
        "human_flows",
        "agent_interactive_flows",
        "agent_bulk_flows",
        "payload_bytes",
        "socket_buffer_bytes",
        "max_packets",
    ):
        _positive_int(raw[name], name)
    if not 1024 <= raw["destination_port"] <= 65535:
        raise ValueError("destination_port must be an unprivileged UDP port")
    if not 16 <= raw["payload_bytes"] <= 1400:
        raise ValueError("payload_bytes must fit the measurement header and path MTU")
    for name in ("drain_s", "offered_load_multiplier", "poll_s", "agent_call_s"):
        _positive_float(raw[name], name)
    if raw["drain_s"] >= 1 or raw["poll_s"] >= 1:
        raise ValueError("drain_s and poll_s must be less than one second")
    parallelism = raw["burst_parallelism"]
    if not isinstance(parallelism, dict) or set(parallelism) != set(BURST_INTENSITIES):
        raise ValueError("burst_parallelism must define every frozen burst intensity")
    for name, value in parallelism.items():
        _positive_int(value, name)
    rules = raw["diffserv_rules"]
    if not isinstance(rules, list) or not rules:
        raise ValueError("diffserv_rules must be nonempty")
    seen: set[tuple[str, int]] = set()
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) != {
            "protocol",
            "destination_port",
            "traffic_class",
        }:
            raise ValueError("invalid DiffServ port rule")
        key = (rule["protocol"], rule["destination_port"])
        if key in seen or key[0] not in ("tcp", "udp"):
            raise ValueError("duplicate or invalid DiffServ port rule")
        seen.add(key)
        _positive_int(key[1], "rule destination_port")
        if key[1] > 65535 or type(rule["traffic_class"]) is not int:
            raise ValueError("invalid DiffServ port or class")
        try:
            TrafficClass(rule["traffic_class"])
        except ValueError as exc:
            raise ValueError("invalid DiffServ class") from exc
    return raw


def queue_rate_pps(
    profile: dict[str, Any], link_mbps: int, plan: dict[str, Any] | None = None
) -> int:
    """Convert link capacity to BMv2 packet units using the planned mean frame size."""
    if type(link_mbps) is not int or link_mbps <= 0:
        raise ValueError("link_mbps must be positive")
    if "traffic_shapes" in profile:
        if plan is None:
            raise ValueError("shaped queue rate requires the measured workload plan")
        sizes = [size + WIRE_BYTES for flow in plan["flows"] for size in payload_sizes(flow)]
        if not sizes:
            raise ValueError("queue rate requires a nonempty schedule")
        return max(1, link_mbps * 1_000_000 * len(sizes) // (8 * sum(sizes)))
    return link_mbps * 1_000_000 // (8 * (profile["payload_bytes"] + WIRE_BYTES))


def archive_inputs(output: Path, sources: Mapping[str, Path]) -> dict[str, str]:
    """Copy exact experiment inputs and record their hashes before network allocation."""
    if not sources or set(sources) - {"profile", "model", "pipeline"}:
        raise ValueError("unknown or missing workload input")
    archive = output / "inputs"
    archive.mkdir(exist_ok=False)
    hashes = {}
    for name, source in sources.items():
        raw = source.read_bytes()
        hashes[name] = hashlib.sha256(raw).hexdigest()
        with (archive / f"{name}.json").open("xb") as stream:
            stream.write(raw)
    with (archive / "sha256.json").open("x", encoding="utf-8") as stream:
        json.dump(hashes, stream, indent=2, sort_keys=True)
        stream.write("\n")
    return hashes


def build_plan(
    profile: dict[str, Any],
    *,
    seed: int,
    share: int,
    burst: str,
    duration: int,
    link_mbps: int,
) -> dict[str, Any]:
    """Build bounded seed-deterministic flow schedules for a measured cell."""
    if (
        type(seed) is not int
        or not 0 <= seed <= 0xFFFF_FFFF
        or type(share) is not int
        or not 0 < share < 100
    ):
        raise ValueError("seed and agent share must be valid integers")
    if burst not in BURST_INTENSITIES or type(duration) is not int or duration <= 0:
        raise ValueError("invalid burst or duration")
    if type(link_mbps) is not int or not 10 <= link_mbps <= 50:
        raise ValueError("invalid link capacity")
    horizon = duration - profile["drain_s"]
    if horizon <= 0:
        raise ValueError("duration must exceed drain_s")
    rng = random.Random(seed)
    agent_ip = next(h.ip_address.split("/")[0] for h in HOSTS if h.role == "agent")
    human_ip = next(h.ip_address.split("/")[0] for h in HOSTS if h.role == "human")
    target_ip = next(h.ip_address.split("/")[0] for h in HOSTS if h.role == "target")
    flow_specs = (
        [(TrafficClass.HUMAN_INTERACTIVE, human_ip)] * profile["human_flows"]
        + [(TrafficClass.AGENT_INTERACTIVE, agent_ip)]
        * (profile["agent_interactive_flows"] * profile["burst_parallelism"][burst])
        + [(TrafficClass.AGENT_BULK, agent_ip)]
        * (profile["agent_bulk_flows"] * profile["burst_parallelism"][burst])
    )
    total_rate = link_mbps * 1_000_000 * profile["offered_load_multiplier"]
    if "traffic_shapes" in profile:
        flows = _shaped_flows(profile, rng, seed, share, horizon, total_rate, flow_specs, target_ip)
        return {
            "provenance": profile["provenance"],
            "seed": seed,
            "share": share,
            "burst": burst,
            "duration_s": duration,
            "link_mbps": link_mbps,
            "flows": flows,
        }
    human_count = profile["human_flows"]
    agent_count = len(flow_specs) - human_count
    flows: list[dict[str, Any]] = []
    packet_count = 0
    for label, src_ip in flow_specs:
        fraction = (
            (1 - share / 100) / human_count
            if label == TrafficClass.HUMAN_INTERACTIVE
            else (share / 100) / agent_count
        )
        rate = total_rate * fraction
        interval = profile["payload_bytes"] * 8 / rate
        phase = rng.random() * min(interval, 0.01)
        offsets: list[float] = []
        position = phase
        while position < horizon:
            offsets.append(round(position, 9))
            position += interval
            if len(offsets) + packet_count > profile["max_packets"]:
                raise ValueError("planned packet count exceeds profile max_packets")
        packet_count += len(offsets)
        for call_offsets in _calls(offsets, label, profile["agent_call_s"], rng):
            index = len(flows) + 1
            flows.append(
                {
                    "flow_id": f"seed-{seed}-flow-{index}",
                    "wire_id": index,
                    "truth": int(label),
                    "src_ip": src_ip,
                    "dst_ip": target_ip,
                    "src_port": 33000 + index,
                    "dst_port": profile["destination_port"],
                    "payload_bytes": profile["payload_bytes"],
                    "mean_bps": rate,
                    "offsets_s": call_offsets,
                }
            )
    if 33000 + len(flows) > 65535:
        raise ValueError("too many source ports")
    return {
        "provenance": profile["provenance"],
        "seed": seed,
        "share": share,
        "burst": burst,
        "duration_s": duration,
        "link_mbps": link_mbps,
        "flows": flows,
    }


def _shaped_flows(
    profile: dict[str, Any],
    rng: random.Random,
    seed: int,
    share: int,
    horizon: float,
    total_rate: float,
    flow_specs: list[tuple[TrafficClass, str]],
    target_ip: str,
) -> list[dict[str, Any]]:
    shapes_for_class = {
        TrafficClass.HUMAN_INTERACTIVE: ("human_video_call",),
        TrafficClass.AGENT_INTERACTIVE: ("agent_mcp",),
        TrafficClass.AGENT_BULK: ("agent_ingest", "agent_crawler"),
    }
    flows: list[dict[str, Any]] = []
    packet_count = 0
    human_count = profile["human_flows"]
    agent_count = len(flow_specs) - human_count
    for label, src_ip in flow_specs:
        fraction = (
            (1 - share / 100) / human_count
            if label == TrafficClass.HUMAN_INTERACTIVE
            else share / 100 / agent_count
        )
        names = shapes_for_class[label]
        for name in names:
            remaining = int(total_rate * fraction * horizon / (8 * len(names)))
            while remaining >= HEADER.size + WIRE_BYTES:
                emitted = False
                for packets in lane_schedules(
                    name,
                    profile["traffic_shapes"][name],
                    horizon,
                    rng,
                    profile["max_packets"],
                    wire_budget=remaining,
                ):
                    selected = take_packet_budget(packets, remaining)
                    for packet in selected:
                        remaining -= packet.payload_bytes + WIRE_BYTES
                        packet_count += 1
                        if packet_count > profile["max_packets"]:
                            raise ValueError("planned packet count exceeds profile max_packets")
                    if not selected:
                        # A residual smaller than one shape packet is unavoidable quantization.
                        if packets:
                            remaining = 0
                            break
                        continue
                    emitted = True
                    index = len(flows) + 1
                    if 33000 + index > 65535:
                        raise ValueError("too many source ports")
                    # §7.20: truth comes from orchestration, never packet features or DSCP.
                    flows.append(
                        {
                            "flow_id": f"seed-{seed}-flow-{index}",
                            "wire_id": index,
                            "truth": int(label),
                            "shape": name,
                            "src_ip": src_ip,
                            "dst_ip": target_ip,
                            "src_port": 33000 + index,
                            "dst_port": profile["destination_port"],
                            "payload_bytes": profile["payload_bytes"],
                            "payload_sizes_bytes": [packet.payload_bytes for packet in selected],
                            "mean_bps": sum(p.payload_bytes + WIRE_BYTES for p in selected)
                            * 8
                            / horizon,
                            "offsets_s": [packet.offset_s for packet in selected],
                        }
                    )
                    if remaining < HEADER.size + WIRE_BYTES:
                        break
                if not emitted:
                    # Quantization may leave too little for another wave; check class totals below.
                    break
    for label in TRAINING_LABELS:
        fraction = (
            1 - share / 100
            if label == TrafficClass.HUMAN_INTERACTIVE
            else share / 100 * sum(item[0] == label for item in flow_specs) / agent_count
        )
        expected = total_rate * fraction * horizon / 8
        actual = sum(
            size + WIRE_BYTES
            for flow in flows
            if flow["truth"] == label
            for size in flow["payload_sizes_bytes"]
        )
        if abs(actual - expected) > expected * 0.02:
            raise ValueError(
                f"shape {label.name} cannot meet offered-load tolerance in this window"
            )
    return flows


def _calls(
    offsets: list[float], label: TrafficClass, call_s: float, rng: random.Random
) -> list[list[float]]:
    """Split an agent lane into consecutive tool calls; the human call stays one flow.

    Each call is a new flow (fresh source port), as an agent opens a connection per tool
    call. A per-lane shift keeps parallel lanes from dialling in lockstep.
    """
    if label == TrafficClass.HUMAN_INTERACTIVE:
        return [offsets]
    shift = rng.random() * call_s
    calls: dict[int, list[float]] = {}
    for offset in offsets:
        calls.setdefault(int((offset + shift) // call_s), []).append(offset)
    return [calls[key] for key in sorted(calls)]


def _rows(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"invalid packet log: {path}")
    return rows


def observations_from_logs(
    plan: dict[str, Any],
    send_logs: list[Path],
    receive_log: Path,
    start: float,
    end: float,
) -> dict[str, Any]:
    """Join actual sender/receiver evidence without inventing lost arrivals."""
    from harness.traffic import packet_id

    if not start < end:
        raise ValueError("invalid measurement window")
    flow_by_id = {flow["flow_id"]: flow for flow in plan["flows"]}
    sizes_by_flow = {name: payload_sizes(flow) for name, flow in flow_by_id.items()}
    sent: dict[str, dict[str, Any]] = {}
    for path in send_logs:
        for row in _rows(path):
            flow = flow_by_id.get(row.get("flow_id"))
            if flow is None or type(row.get("sequence")) is not int:
                raise ValueError("sender log contains foreign flow or sequence")
            identity = packet_id(plan["seed"], flow["wire_id"], row["sequence"])
            if row.get("packet_id") != identity or identity in sent:
                raise ValueError("sender log contains duplicate or corrupt identity")
            sizes = sizes_by_flow[flow["flow_id"]]
            if not 1 <= row["sequence"] <= len(sizes):
                raise ValueError("sender sequence outside planned schedule")
            if row.get("size_bytes") != sizes[row["sequence"] - 1] + WIRE_BYTES:
                raise ValueError("sender packet size differs from plan")
            if not start <= row["sent_s"] <= end:
                raise ValueError("sender timestamp outside measurement window")
            sent[identity] = row
    received: dict[str, dict[str, Any]] = {}
    for row in _rows(receive_log):
        identity = row.get("packet_id")
        if identity in received or identity not in sent:
            raise ValueError("receiver contains duplicate or foreign identity")
        source = sent[identity]
        flow = flow_by_id[source["flow_id"]]
        if (
            any(
                row.get(name) != flow[name] for name in ("src_ip", "src_port", "dst_ip", "dst_port")
            )
            or row.get("size_bytes") != source["size_bytes"]
        ):
            raise ValueError("receiver tuple or size differs from sender plan")
        if not source["sent_s"] <= row["received_s"] <= end:
            raise ValueError("receiver timestamp precedes sender or exceeds window")
        received[identity] = row
    packets = []
    classifications = []
    dscp_to_class = {treatment.dscp: label for label, treatment in CLASS_TREATMENTS.items()}
    for flow in plan["flows"]:
        flow_rows = sorted(
            (row for row in sent.values() if row["flow_id"] == flow["flow_id"]),
            key=lambda row: row["sequence"],
        )
        for row in flow_rows:
            arrival = received.get(row["packet_id"])
            packets.append(
                {
                    "packet_id": row["packet_id"],
                    "traffic_class": flow["truth"],
                    "sent_s": row["sent_s"],
                    "received_s": None if arrival is None else arrival["received_s"],
                    "size_bytes": row["size_bytes"],
                }
            )
        for checkpoint in FEATURE_CHECKPOINTS:
            if len(flow_rows) < checkpoint or flow["truth"] not in TRAINING_LABELS:
                continue
            arrivals = [
                received[row["packet_id"]]
                for row in flow_rows[:checkpoint]
                if row["packet_id"] in received
            ]
            # A missing checkpoint datagram cannot support a checkpoint prediction.
            if not arrivals or flow_rows[checkpoint - 1]["packet_id"] not in received:
                continue
            last = arrivals[-1]
            predicted = dscp_to_class.get(last["tos"] >> 2, TrafficClass.UNKNOWN)
            classifications.append(
                {
                    "flow_id": flow["flow_id"],
                    "checkpoint": checkpoint,
                    "truth": flow["truth"],
                    "predicted": int(predicted),
                    "label_source": "orchestration",
                }
            )
    return {
        "measurement_start_s": start,
        "measurement_end_s": end,
        "packets": packets,
        "tool_calls": [],
        "classifications": classifications,
    }


def _wait_for_ready(path: Path, child: subprocess.Popen[Any]) -> None:
    deadline = time.monotonic() + 5
    while not path.exists():
        if child.poll() is not None:
            raise RuntimeError("target exited before accepting UDP measurement traffic")
        if time.monotonic() >= deadline:
            raise TimeoutError("target did not become ready within five seconds")
        time.sleep(0.01)


def _run_network_traffic(
    network: Any, plan: dict[str, Any], profile: dict[str, Any], output: Path
) -> dict[str, Any]:
    target = next(host.name for host in HOSTS if host.role == "target")
    start = time.time() + 1.0
    end = start + plan["duration_s"]
    target_plan = {
        "bind_ip": plan["flows"][0]["dst_ip"],
        "ports": [profile["destination_port"]],
        "end_s": end,
        "socket_buffer_bytes": profile["socket_buffer_bytes"],
        "poll_s": profile["poll_s"],
    }
    target_path = output / "target-plan.json"
    target_path.write_text(json.dumps(target_plan), encoding="utf-8")
    receiver_log = output / "receiver.jsonl"
    ready = output / "target-ready.json"
    target_child = network.get(target).popen(
        [
            sys.executable,
            "-m",
            "harness.traffic",
            "target",
            "--plan",
            str(target_path),
            "--log",
            str(receiver_log),
            "--ready",
            str(ready),
        ],
        stdout=(output / "target.stdout.log").open("x"),
        stderr=(output / "target.stderr.log").open("x"),
    )
    children: list[subprocess.Popen[Any]] = [target_child]
    send_logs: list[Path] = []
    try:
        _wait_for_ready(ready, target_child)
        for host in HOSTS:
            flows = [
                flow for flow in plan["flows"] if flow["src_ip"] == host.ip_address.split("/")[0]
            ]
            if not flows:
                continue
            actor = {
                "seed": plan["seed"],
                "start_s": start,
                "end_s": end,
                "socket_buffer_bytes": profile["socket_buffer_bytes"],
                "flows": flows,
            }
            actor_path = output / f"{host.name}-plan.json"
            actor_path.write_text(json.dumps(actor), encoding="utf-8")
            send_log = output / f"{host.name}-sender.jsonl"
            send_logs.append(send_log)
            children.append(
                network.get(host.name).popen(
                    [
                        sys.executable,
                        "-m",
                        "harness.traffic",
                        "sender",
                        "--plan",
                        str(actor_path),
                        "--log",
                        str(send_log),
                    ],
                    stdout=(output / f"{host.name}.stdout.log").open("x"),
                    stderr=(output / f"{host.name}.stderr.log").open("x"),
                )
            )
        for child in children[1:]:
            if child.wait(timeout=plan["duration_s"] + 10):
                raise RuntimeError("measurement sender failed; inspect actor stderr log")
        if target_child.wait(timeout=profile["drain_s"] + 10):
            raise RuntimeError("measurement target failed; inspect target stderr log")
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
    return observations_from_logs(plan, send_logs, receiver_log, start, end)


def main(argv: Sequence[str] | None = None) -> int:
    """Materialize a seeded profile plan without replacing Jobin's experiment runtime.

    Args:
        argv: Optional command-line arguments.

    Returns:
        Zero for a newly archived plan; one for an invalid or already existing destination.
    """
    from eval.run_experiment import load_config

    parser = argparse.ArgumentParser(description="Build an application-shaped UDP traffic plan")
    parser.add_argument("--plan-only", action="store_true", required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--profile", type=Path, default=Path("eval/configs/workload_demo.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--share", type=int, required=True)
    parser.add_argument("--burst", choices=BURST_INTENSITIES, required=True)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        config = load_config(args.config)
        if (
            args.seed not in config.seeds
            or args.share not in config.agent_share_pct
            or args.burst not in config.burst_intensity
        ):
            raise ValueError("selected seed/share/burst is outside the experiment config")
        profile = load_profile(args.profile)
        plan = build_plan(
            profile,
            seed=args.seed,
            share=args.share,
            burst=args.burst,
            duration=config.duration_s,
            link_mbps=config.link_mbps,
        )
        plan["queue_rate_pps"] = queue_rate_pps(profile, config.link_mbps, plan)
        # §7.19: plans/inputs are append-only and do not attest any live experiment result.
        args.output.mkdir(parents=True, exist_ok=False)
        archive_inputs(args.output, {"profile": args.profile})
        with (args.output / "config.yaml").open("xb") as stream:
            stream.write(args.config.read_bytes())
        with (args.output / "workload-plan.json").open("x", encoding="utf-8") as stream:
            json.dump(plan, stream, indent=2)
            stream.write("\n")
    except (OSError, ValueError) as exc:
        LOGGER.error("could not build workload plan: %s", exc)
        return 1
    LOGGER.info("wrote workload plan: %s", args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
