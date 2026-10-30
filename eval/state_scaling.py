"""P4.4 seeded scaling traces and analysis of real two-tap BMv2 evidence."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import math
import random
from collections import Counter
from pathlib import Path
from typing import Any

from scapy.all import IP, UDP, Ether, PcapWriter, Raw

from common.contracts import (
    COLLISION_COUNTER_REGISTER,
    FEATURE_CHECKPOINTS,
    FLOW_KEY_REGISTER,
    FLOW_SLOTS,
    IDLE_TIMEOUT_US,
    LAST_TIMESTAMP_REGISTER,
    MAX_AGENT_SHARE_PCT,
    MIN_AGENT_SHARE_PCT,
    PACKET_COUNT_REGISTER,
    TIMESTAMP_BITS,
    TrafficClass,
)
from common.feature_math import flow_key_bytes, flow_slot, flow_tag
from eval.metrics import summarize_observations, write_json
from eval.overhead import pipeline_footprint
from eval.pcap_metrics import _tap_packets, observations_from_pcaps
from eval.run_experiment import load_config
from harness.capture import FlowLabel, write_labels
from harness.topology import HOSTS
from harness.traffic import HEADER


def load_profile(path: Path) -> dict[str, Any]:
    """Validate local scaling inputs without changing the frozen experiment YAML fields."""
    raw = json.loads(path.read_text())
    if not isinstance(raw, dict) or set(raw) != {
        "flow_counts",
        "packets_per_flow",
        "offered_pps",
        "payload_bytes",
        "destination_port",
        "drain_s",
        "provenance",
    }:
        raise ValueError("invalid scaling profile fields")
    counts = raw["flow_counts"]
    if (
        not isinstance(counts, list)
        or not counts
        or any(type(n) is not int or not 1 <= n <= 100000 for n in counts)
        or counts != sorted(set(counts))
    ):
        raise ValueError("flow_counts must be unique ascending integers in 1..100000")
    for name in ("offered_pps", "payload_bytes", "destination_port", "packets_per_flow"):
        if type(raw[name]) is not int or raw[name] <= 0:
            raise ValueError(f"{name} must be a positive integer")
    if raw["packets_per_flow"] not in FEATURE_CHECKPOINTS:
        raise ValueError("packets_per_flow must be a frozen checkpoint")
    if not HEADER.size <= raw["payload_bytes"] <= 1400:
        raise ValueError("payload_bytes exceeds measurement header/MTU bounds")
    if not 1024 <= raw["destination_port"] <= 65535:
        raise ValueError("destination_port must be an unprivileged UDP port")
    if raw["provenance"] != "synthetic_udp_state_scaling":
        raise ValueError("scaling profile must identify synthetic stress provenance")
    drain = raw["drain_s"]
    if type(drain) not in (int, float) or not math.isfinite(drain) or drain <= 0:
        raise ValueError("drain_s must be positive and finite")
    return raw


def generated_keys(count: int, seed: int, destination_port: int) -> list[bytes]:
    """Generate unique synthetic source addresses with a reproducible seeded tuple order."""
    if type(count) is not int or not 1 <= count <= 100000:
        raise ValueError("flow count must be in 1..100000")
    if type(seed) is not int or not 0 <= seed <= 0xFFFF_FFFF:
        raise ValueError("seed must fit the measurement header")
    if type(destination_port) is not int or not 1024 <= destination_port <= 65535:
        raise ValueError("invalid destination port")
    rng = random.Random(seed)
    target = next(host for host in HOSTS if host.role == "target")
    server_ip = int(ipaddress.IPv4Interface(target.ip_address).ip)
    # §7.20: tuple identities are generated before any classifier prediction or labelling.
    sources = rng.sample(range(1, 1 << 22), count)
    return [
        flow_key_bytes(
            0x0A400000 + source, server_ip, 17, rng.randrange(1024, 65536), destination_port
        )
        for source in sources
    ]


def planned_collisions(keys: list[bytes]) -> dict[str, int]:
    """Model first-owner occupancy with no aging; this is explicitly not switch evidence."""
    owners: dict[int, tuple[int, bytes]] = {}
    collisions = aliases = 0
    for key in keys:
        slot, tag = flow_slot(key), flow_tag(key)
        # §7.1: a live collider cannot replace the owner; full-key aliases are reported too.
        if slot not in owners:
            owners[slot] = tag, key
        elif owners[slot][0] != tag:
            collisions += 1
        elif owners[slot][1] != key:
            aliases += 1
    return {
        "occupied_slots": len(owners),
        "colliding_new_flows": collisions,
        "ownership_tag_aliases": aliases,
    }


def load_prepared(path: Path) -> dict[str, Any]:
    """Validate replay inputs before launching or interpreting a real experiment."""
    metadata = json.loads((path / "prepared.json").read_text())
    expected_files = {"traffic.pcap", "labels.csv", "profile.json"}
    if set(metadata["sha256"]) != expected_files:
        raise ValueError("prepared scaling files differ from the expected schema")
    for name, digest in metadata["sha256"].items():
        if hashlib.sha256((path / name).read_bytes()).hexdigest() != digest:
            raise ValueError("prepared scaling input hash mismatch")
    profile = load_profile(path / "profile.json")
    if profile != metadata["profile"] or metadata["flow_count"] not in profile["flow_counts"]:
        raise ValueError("prepared scaling profile differs from metadata")
    if type(metadata["seed"]) is not int or not 0 <= metadata["seed"] <= 0xFFFF_FFFF:
        raise ValueError("invalid prepared seed")
    share = metadata["agent_share_pct"]
    if type(share) is not int or not MIN_AGENT_SHARE_PCT <= share <= MAX_AGENT_SHARE_PCT:
        raise ValueError("invalid prepared agent share")
    return metadata


def prepare(profile_path: Path, count: int, seed: int, output: Path, share: int) -> None:
    """Write seeded replay input and frozen CSV labels, retaining their provenance/hashes."""
    profile = load_profile(profile_path)
    if count not in profile["flow_counts"]:
        raise ValueError("flow count is not configured in the profile")
    if type(share) is not int or not MIN_AGENT_SHARE_PCT <= share <= MAX_AGENT_SHARE_PCT:
        raise ValueError("share must match the frozen agent-share range")
    keys = generated_keys(count, seed, profile["destination_port"])
    bulk = count * share // 100
    truths = [int(TrafficClass.AGENT_BULK)] * bulk + [int(TrafficClass.HUMAN_INTERACTIVE)] * (
        count - bulk
    )
    random.Random(seed).shuffle(truths)
    output.mkdir(parents=True, exist_ok=False)
    trace = output / "traffic.pcap"
    source = next(host for host in HOSTS if host.role == "agent")
    target = next(host for host in HOSTS if host.role == "target")
    prefixes = []
    labels = []
    for index, key in enumerate(keys, start=1):
        src_ip, dst_ip = str(ipaddress.IPv4Address(key[:4])), str(ipaddress.IPv4Address(key[4:8]))
        src_port, dst_port = int.from_bytes(key[9:11], "big"), int.from_bytes(key[11:13], "big")
        packet = (
            Ether(src=source.mac_address, dst=target.mac_address)
            / IP(src=src_ip, dst=dst_ip)
            / UDP(sport=src_port, dport=dst_port, chksum=0)
            / Raw(bytes(profile["payload_bytes"]))
        )
        prefixes.append(bytes(packet)[:42])
        labels.append(
            FlowLabel(
                f"scaling-{seed}-{index}",
                src_ip,
                dst_ip,
                17,
                src_port,
                dst_port,
                truths[index - 1],
                "synthetic:state_scaling",
                trace.name,
            )
        )
    padding = bytes(profile["payload_bytes"] - HEADER.size)
    writer = PcapWriter(str(trace), linktype=1, sync=False)
    try:
        for sequence in range(profile["packets_per_flow"]):
            for index, prefix in enumerate(prefixes):
                packet = Ether(prefix + HEADER.pack(seed, index + 1, sequence) + padding)
                packet.time = 1 + (sequence * count + index) / profile["offered_pps"]
                writer.write(packet)
    finally:
        writer.close()
    write_labels(tuple(labels), output / "labels.csv")
    (output / "profile.json").write_bytes(profile_path.read_bytes())
    write_json(
        output / "prepared.json",
        {
            "provenance": "synthetic_udp_scaling_input_not_measurement",
            "agent_share_pct": share,
            "seed": seed,
            "flow_count": count,
            "profile": profile,
            "planned_hash_model": planned_collisions(keys),
            "planned_cycle_s": count / profile["offered_pps"],
            "sha256": {
                name: hashlib.sha256((output / name).read_bytes()).hexdigest()
                for name in ("traffic.pcap", "labels.csv", "profile.json")
            },
        },
    )


def capture_concurrency(ingress: Path) -> dict[str, Any]:
    """Measure offered active flows and refresh gaps from actual ingress packet timestamps."""
    last: dict[object, float] = {}
    maximum_gap = 0.0
    events = []
    for packet in _tap_packets(ingress):
        timestamp = float(packet.timestamp)
        if events and timestamp < events[-1][0]:
            raise ValueError("scaling ingress timestamps must be monotonic")
        if packet.flow in last:
            maximum_gap = max(maximum_gap, timestamp - last[packet.flow])
        last[packet.flow] = timestamp
        events.append((timestamp, packet.flow))
    # §7.11: a configured flow count is not evidence of activity inside the idle window.
    active: Counter[object] = Counter()
    left = peak = 0
    for timestamp, flow in events:
        active[flow] += 1
        while events[left][0] < timestamp - IDLE_TIMEOUT_US / 1e6:
            retired = events[left][1]
            active[retired] -= 1
            if active[retired] == 0:
                del active[retired]
            left += 1
        peak = max(peak, len(active))
    return {
        "distinct_ingress_flows": len(last),
        "peak_offered_active_flows": peak,
        "max_observed_refresh_gap_s": maximum_gap,
        "refresh_within_idle_timeout": maximum_gap <= IDLE_TIMEOUT_US / 1e6,
        "note": "Ingress offered activity, not proof that BMv2 processed without backlog",
    }


def analyze(
    prepared: Path,
    ingress: Path,
    egress: Path,
    snapshot: Path,
    pipeline: Path,
    start: float,
    end: float,
    output: Path,
) -> dict[str, Any]:
    """Analyze real pcaps and captured register snapshots; never substitute planned counters."""
    metadata = load_prepared(prepared)
    raw = observations_from_pcaps(ingress, egress, prepared / "labels.csv", start, end)
    registers = json.loads(snapshot.read_text())
    before, after = registers["before"], registers["after"]
    initial = before[COLLISION_COUNTER_REGISTER]
    final = after[COLLISION_COUNTER_REGISTER]
    if (
        initial != [0]
        or type(initial[0]) is not int
        or len(final) != 1
        or type(final[0]) is not int
        or not 0 <= final[0] < 0xFFFF_FFFF
    ):
        raise ValueError("scaling requires a fresh, unsaturated collision counter")
    for name in (FLOW_KEY_REGISTER, PACKET_COUNT_REGISTER, LAST_TIMESTAMP_REGISTER):
        if len(after[name]) != FLOW_SLOTS:
            raise ValueError(f"invalid scaling register snapshot: {name}")
        width = TIMESTAMP_BITS if name == LAST_TIMESTAMP_REGISTER else 32
        if any(type(value) is not int or not 0 <= value < 1 << width for value in after[name]):
            raise ValueError(f"invalid scaling register values: {name}")
    if any(packet["traffic_class"] == int(TrafficClass.UNKNOWN) for packet in raw["packets"]):
        raise ValueError("ingress contains traffic outside the labelled scaling workload")
    concurrency = capture_concurrency(ingress)
    expected = metadata["flow_count"]
    summary = summarize_observations(raw)
    if final[0] > len(raw["packets"]):
        raise ValueError("collision events exceed captured ingress packets; capture is incomplete")
    accuracy = {}
    for checkpoint in FEATURE_CHECKPOINTS:
        if checkpoint > metadata["profile"]["packets_per_flow"]:
            accuracy[str(checkpoint)] = None
            continue
        predictions = [row for row in raw["classifications"] if row["checkpoint"] == checkpoint]
        if len(predictions) > expected:
            raise ValueError("capture contains more classified flows than were prepared")
        correct = sum(row["truth"] == row["predicted"] for row in predictions)
        accuracy[str(checkpoint)] = {
            "observed": len(predictions),
            "missing": expected - len(predictions),
            "accuracy_received": correct / len(predictions) if predictions else None,
            "correct_fraction_all_offered": correct / expected,
        }
    report = {
        "provenance": "real_switch_captures_synthetic_stress_workload",
        "seed": metadata["seed"],
        "requested_flows": expected,
        "concurrency": concurrency,
        "requested_concurrency_observed": concurrency["peak_offered_active_flows"] == expected
        and concurrency["refresh_within_idle_timeout"],
        "collision_packet_events": final[0],
        "collision_events_per_ingress_packet": final[0] / len(raw["packets"]),
        "occupied_slots_after": sum(count > 0 for count in after[PACKET_COUNT_REGISTER]),
        "planned_hash_model": metadata["planned_hash_model"],
        "accuracy": accuracy,
        "footprint": pipeline_footprint(pipeline),
        "switch_rss_bytes": registers.get("switch_rss_bytes"),
        "conditions": registers.get("conditions"),
        "summary": summary,
        "note": "Collision register counts packet events, not distinct colliding flows; "
        "accuracy is synthetic stress only. Logical register bits are not resident RSS.",
        "sources_sha256": {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (ingress, egress, snapshot, pipeline)
        },
    }
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "observations.json", raw)
    write_json(output / "scaling-report.json", report)
    return report


def prepare_sweep(experiment_path: Path, profile_path: Path, output: Path, plan_only: bool) -> None:
    """Prepare configured cells or write an explicitly analytical ownership plan."""
    experiment = load_config(experiment_path)
    profile = load_profile(profile_path)
    if len(experiment.agent_share_pct) != 1 or experiment.burst_intensity != ("low",):
        raise ValueError("scaling uses one configured class share and low burst")
    output.mkdir(parents=True, exist_ok=False)
    if plan_only:
        rows = [
            {
                "seed": seed,
                "flows": count,
                "planned_hash_model": planned_collisions(
                    generated_keys(count, seed, profile["destination_port"])
                ),
            }
            for seed in experiment.seeds
            for count in profile["flow_counts"]
        ]
        write_json(
            output / "plan.json",
            {
                "provenance": "analytical_hash_plan_not_switch_measurement",
                "rows": rows,
                "measured_collision_events": None,
                "measured_accuracy": None,
                "profile_sha256": hashlib.sha256(profile_path.read_bytes()).hexdigest(),
                "experiment_sha256": hashlib.sha256(experiment_path.read_bytes()).hexdigest(),
            },
        )
        return
    for seed in experiment.seeds:
        for count in profile["flow_counts"]:
            prepare(
                profile_path,
                count,
                seed,
                output / f"seed-{seed}" / f"flows-{count}",
                experiment.agent_share_pct[0],
            )


def main() -> None:
    """Prepare an input trace or analyze immutable live scaling evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    prepare_cmd = sub.add_parser("prepare")
    prepare_cmd.add_argument(
        "--profile", type=Path, default=Path("eval/configs/state_scaling.json")
    )
    prepare_cmd.add_argument("--flows", type=int, required=True)
    prepare_cmd.add_argument("--seed", type=int, required=True)
    prepare_cmd.add_argument("--share", type=int, required=True)
    prepare_cmd.add_argument("--output", type=Path, required=True)
    sweep_cmd = sub.add_parser("prepare-sweep")
    sweep_cmd.add_argument("--experiment", type=Path, required=True)
    sweep_cmd.add_argument("--profile", type=Path, default=Path("eval/configs/state_scaling.json"))
    sweep_cmd.add_argument("--output", type=Path, required=True)
    sweep_cmd.add_argument("--plan-only", action="store_true")
    analyze_cmd = sub.add_parser("analyze")
    for name in ("prepared", "ingress", "egress", "snapshot", "pipeline", "output"):
        analyze_cmd.add_argument(f"--{name}", type=Path, required=True)
    analyze_cmd.add_argument("--start", type=float, required=True)
    analyze_cmd.add_argument("--end", type=float, required=True)
    args = parser.parse_args()
    if args.operation == "prepare":
        prepare(args.profile, args.flows, args.seed, args.output, args.share)
    elif args.operation == "prepare-sweep":
        prepare_sweep(args.experiment, args.profile, args.output, args.plan_only)
    else:
        analyze(
            args.prepared,
            args.ingress,
            args.egress,
            args.snapshot,
            args.pipeline,
            args.start,
            args.end,
            args.output,
        )


if __name__ == "__main__":
    main()
