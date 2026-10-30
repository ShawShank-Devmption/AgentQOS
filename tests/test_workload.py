"""Real UDP evidence and deterministic synthetic stress orchestration (P4.1)."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from common.contracts import (
    CLASS_TREATMENTS,
    TrafficClass,
)
from eval import workload
from harness import traffic

PROFILE = Path("eval/configs/workload_runtime.json")


def test_fixed_udp_packet_rate_matches_the_configured_link_capacity() -> None:
    profile = workload.load_profile(PROFILE)
    rate = workload.queue_rate_pps(profile, 20)
    assert rate == 20_000_000 // (8 * (profile["payload_bytes"] + 42))
    assert rate > 0


def test_diffserv_marks_the_shared_service_port_without_truth_labels() -> None:
    profile = workload.load_profile(PROFILE)
    assert profile["diffserv_rules"] == [
        {
            "protocol": "udp",
            "destination_port": profile["destination_port"],
            "traffic_class": int(TrafficClass.HUMAN_INTERACTIVE),
        }
    ]


def test_workload_archives_exact_profile_model_and_pipeline_bytes(tmp_path: Path) -> None:
    sources = {}
    for name in ("profile", "model", "pipeline"):
        source = tmp_path / f"{name}.json"
        source.write_text(name)
        sources[name] = source
    output = tmp_path / "run"
    output.mkdir()
    manifest = workload.archive_inputs(output, sources)
    for name, source in sources.items():
        assert (output / "inputs" / f"{name}.json").read_bytes() == source.read_bytes()
        assert manifest[name] == hashlib.sha256(source.read_bytes()).hexdigest()
    sources["model"].write_text("changed")
    assert (output / "inputs" / "model.json").read_text() == "model"


def test_plan_is_seeded_and_preserves_share_and_flow_identity() -> None:
    profile = workload.load_profile(PROFILE)
    plan = workload.build_plan(profile, seed=7, share=30, burst="med", duration=2, link_mbps=20)
    assert plan == workload.build_plan(
        profile, seed=7, share=30, burst="med", duration=2, link_mbps=20
    )
    assert plan != workload.build_plan(
        profile, seed=8, share=30, burst="med", duration=2, link_mbps=20
    )
    flows = plan["flows"]
    tuples = [(f["src_ip"], f["src_port"], f["dst_ip"], f["dst_port"]) for f in flows]
    assert len(tuples) == len(set(tuples))
    assert len({f["dst_port"] for f in flows}) == 1
    human_packets = sum(len(f["offsets_s"]) for f in flows if f["truth"] == 1)
    agent_packets = sum(len(f["offsets_s"]) for f in flows if f["truth"] in (2, 3))
    assert agent_packets / (agent_packets + human_packets) == pytest.approx(0.3, abs=0.01)
    assert all(f["offsets_s"] == sorted(f["offsets_s"]) for f in flows)
    assert all(0 <= t < 2 - profile["drain_s"] for f in flows for t in f["offsets_s"])
    assert plan["provenance"] == "synthetic_udp_stress"


@pytest.mark.parametrize("change", ["unknown", "negative", "duplicate_rules", "packet_budget"])
def test_profile_rejects_invalid_and_unsafe_parameters(tmp_path: Path, change: str) -> None:
    raw = json.loads(PROFILE.read_text())
    if change == "unknown":
        raw["unrecognized"] = 1
    elif change == "negative":
        raw["offered_load_multiplier"] = -1
    elif change == "duplicate_rules":
        raw["diffserv_rules"] *= 2
    else:
        raw["max_packets"] = True
    source = tmp_path / "profile.json"
    source.write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        workload.load_profile(source)


def _evidence(tmp_path: Path) -> tuple[dict, Path, Path]:
    plan = {
        "flows": [
            {
                "flow_id": "flow",
                "wire_id": 4,
                "truth": 1,
                "src_ip": "127.0.0.1",
                "dst_ip": "127.0.0.1",
                "src_port": 33001,
                "dst_port": 33002,
                "payload_bytes": 64,
                "offsets_s": list(range(16)),
            }
        ],
        "seed": 7,
    }
    sender = tmp_path / "send.jsonl"
    receiver = tmp_path / "recv.jsonl"
    sent = []
    received = []
    for seq in range(1, 17):
        packet_id = traffic.packet_id(7, 4, seq)
        sent.append(
            {
                "packet_id": packet_id,
                "flow_id": "flow",
                "sequence": seq,
                "sent_s": 100 + seq / 100,
                "size_bytes": 106,
            }
        )
        if seq != 6:
            received.append(
                {
                    "packet_id": packet_id,
                    "received_s": 100 + seq / 100 + 0.001,
                    "size_bytes": 106,
                    "src_ip": "127.0.0.1",
                    "src_port": 33001,
                    "dst_ip": "127.0.0.1",
                    "dst_port": 33002,
                    "tos": CLASS_TREATMENTS[TrafficClass.HUMAN_INTERACTIVE].dscp << 2,
                }
            )
    sender.write_text("".join(json.dumps(row) + "\n" for row in sent))
    receiver.write_text("".join(json.dumps(row) + "\n" for row in reversed(received)))
    return plan, sender, receiver


def test_join_preserves_loss_and_checkpoint_packet_identity(tmp_path: Path) -> None:
    plan, sender, receiver = _evidence(tmp_path)
    result = workload.observations_from_logs(plan, [sender], receiver, 100, 101)
    assert len(result["packets"]) == 16
    assert result["packets"][5]["received_s"] is None
    assert result["tool_calls"] == []
    assert result["classifications"] == [
        {
            "flow_id": "flow",
            "checkpoint": 16,
            "truth": 1,
            "predicted": 1,
            "label_source": "orchestration",
        }
    ]


@pytest.mark.parametrize("damage", ["duplicate", "unmatched", "tuple", "size", "clock"])
def test_join_rejects_corrupt_or_foreign_evidence(tmp_path: Path, damage: str) -> None:
    plan, sender, receiver = _evidence(tmp_path)
    rows = [json.loads(line) for line in receiver.read_text().splitlines()]
    if damage == "duplicate":
        rows.append(copy.deepcopy(rows[0]))
    elif damage == "unmatched":
        rows[0]["packet_id"] = "foreign"
    elif damage == "tuple":
        rows[0]["src_port"] += 1
    elif damage == "size":
        rows[0]["size_bytes"] += 1
    else:
        rows[0]["received_s"] = 99
    receiver.write_text("".join(json.dumps(row) + "\n" for row in rows))
    with pytest.raises(ValueError):
        workload.observations_from_logs(plan, [sender], receiver, 100, 101)
