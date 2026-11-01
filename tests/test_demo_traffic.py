"""D2: seeded, bounded application-shaped UDP demo traffic."""

from __future__ import annotations

import copy
import json
import random
import socket
from pathlib import Path
from typing import Any

import pytest

from common.contracts import TrafficClass
from eval import workload
from harness import traffic

DEMO_PROFILE = Path("eval/configs/workload_demo.json")


def _demo_profile() -> dict[str, Any]:
    profile = json.loads(Path("eval/configs/workload_runtime.json").read_text())
    profile["traffic_shapes"] = {
        "human_video_call": {
            "frame_interval_s": 0.033333,
            "frame_bytes": {"distribution": "lognormal", "median": 4000, "sigma": 0.5},
            "video_payload_bytes": 1200,
            "video_packet_gap_s": 0.0009,
            "audio_interval_s": 0.02,
            "audio_bytes": {"distribution": "uniform", "min": 90, "max": 159},
            "jitter_mean_s": 0.00004,
        }
    }
    common = {
        "segment_payload_bytes": 1472,
        "segment_gap_s": 0.0006056,
        "max_object_bytes": 4000000,
        "server_delay_s": {"distribution": "lognormal", "median": 0.03, "sigma": 0.8},
        "jitter_mean_s": 0.00004,
        "start_spread_s": 0.02,
    }
    profile["traffic_shapes"]["agent_mcp"] = {
        **common,
        "request_bytes": {"distribution": "lognormal", "median": 600, "sigma": 0.6},
        "response_bytes": {"distribution": "lognormal", "median": 3000, "sigma": 1.0},
        "think_s": {"distribution": "lognormal", "median": 1.2, "sigma": 0.6},
        "calls_per_step_mean": 0.8,
    }
    for name in ("agent_ingest", "agent_crawler"):
        profile["traffic_shapes"][name] = {
            **common,
            "request_bytes": {"distribution": "lognormal", "median": 200000, "sigma": 1.0}
            if name == "agent_ingest"
            else {"distribution": "uniform", "min": 200, "max": 449},
            "response_bytes": {"distribution": "uniform", "min": 200, "max": 1499}
            if name == "agent_ingest"
            else {"distribution": "lognormal", "median": 150000, "sigma": 1.0},
            "requests_per_flow": [3, 9],
            "parallel_flows": [8, 32],
            "wave_gap_s": {"distribution": "lognormal", "median": 1.2, "sigma": 0.6},
            "parse_gap_s": 0.001,
        }
    return profile


def test_existing_loader_accepts_the_demo_extension(tmp_path: Path) -> None:
    raw = _demo_profile()
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(raw))
    assert workload.load_profile(path) == raw


@pytest.mark.parametrize("seed", [1, 7, 99])
@pytest.mark.parametrize("share,burst", [(10, "low"), (50, "med"), (90, "high")])
def test_shaped_schedule_matches_each_class_wire_budget(seed: int, share: int, burst: str) -> None:
    profile = _demo_profile()
    plan = workload.build_plan(
        profile, seed=seed, share=share, burst=burst, duration=3, link_mbps=10
    )
    assert plan == workload.build_plan(
        profile, seed=seed, share=share, burst=burst, duration=3, link_mbps=10
    )
    assert plan != workload.build_plan(
        profile, seed=seed + 1, share=share, burst=burst, duration=3, link_mbps=10
    )
    expected_fractions = {
        TrafficClass.HUMAN_INTERACTIVE: 1 - share / 100,
        TrafficClass.AGENT_INTERACTIVE: share / 200,
        TrafficClass.AGENT_BULK: share / 200,
    }
    identities = []
    for flow in plan["flows"]:
        assert len(flow["payload_sizes_bytes"]) == len(flow["offsets_s"])
        assert all(traffic.HEADER.size <= size <= 1472 for size in flow["payload_sizes_bytes"])
        assert flow["offsets_s"] == sorted(flow["offsets_s"])
        assert all(0 <= offset < 3 - profile["drain_s"] for offset in flow["offsets_s"])
        identities.extend(
            traffic.packet_id(seed, flow["wire_id"], index + 1)
            for index in range(len(flow["offsets_s"]))
        )
    assert len(identities) == len(set(identities))
    assert len({flow["src_port"] for flow in plan["flows"]}) == len(plan["flows"])
    assert {flow["shape"] for flow in plan["flows"]} == set(profile["traffic_shapes"])
    for label, fraction in expected_fractions.items():
        frame_bytes = sum(
            size + traffic.WIRE_BYTES
            for flow in plan["flows"]
            if flow["truth"] == label
            for size in flow["payload_sizes_bytes"]
        )
        offered_bps = frame_bytes * 8 / (3 - profile["drain_s"])
        assert offered_bps == pytest.approx(12_000_000 * fraction, rel=0.02)


@pytest.mark.parametrize("damage", ["unknown", "nan", "boolean", "range", "mtu", "missing"])
def test_demo_profile_validation_rejects_bad_shapes(tmp_path: Path, damage: str) -> None:
    profile = _demo_profile()
    video = profile["traffic_shapes"]["human_video_call"]
    if damage == "unknown":
        video["typo"] = 1
    elif damage == "nan":
        video["frame_interval_s"] = float("nan")
    elif damage == "boolean":
        video["audio_interval_s"] = True
    elif damage == "range":
        video["audio_bytes"]["min"] = 160
    elif damage == "mtu":
        video["video_payload_bytes"] = 1473
    else:
        del profile["traffic_shapes"]["agent_mcp"]
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(profile))
    with pytest.raises(ValueError, match="shape|distribution|payload|interval"):
        workload.load_profile(path)


def test_demo_schedule_enforces_the_total_packet_budget() -> None:
    profile = _demo_profile()
    profile["max_packets"] = 10
    with pytest.raises(ValueError, match="max_packets"):
        workload.build_plan(profile, seed=1, share=50, burst="low", duration=2, link_mbps=10)


def test_video_cadence_is_not_accelerated_to_fill_the_link() -> None:
    from harness.demo_traffic import lane_schedules

    shape = copy.deepcopy(_demo_profile()["traffic_shapes"]["human_video_call"])
    shape["frame_bytes"] = {"distribution": "uniform", "min": 2400, "max": 2400}
    shape["audio_bytes"] = {"distribution": "uniform", "min": 100, "max": 100}
    shape["jitter_mean_s"] = 0
    packets = next(lane_schedules("human_video_call", shape, 0.2, random.Random(1), 100))
    video = [packet.offset_s for packet in packets if packet.payload_bytes == 1200]
    audio = [packet.offset_s for packet in packets if packet.payload_bytes == 100]
    assert video[1] - video[0] == pytest.approx(0.0009)
    assert video[2] - video[0] == pytest.approx(0.033333)
    assert audio[1] - audio[0] == pytest.approx(0.02)


def test_variable_size_sender_emits_real_udp_bytes(tmp_path: Path) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
        receiver.bind(("127.0.0.1", 0))
        receiver.settimeout(1)
        plan = {
            "seed": 7,
            "start_s": 0,
            "end_s": 1e12,
            "socket_buffer_bytes": 65536,
            "flows": [
                {
                    "flow_id": "local",
                    "wire_id": 1,
                    "src_ip": "127.0.0.1",
                    "src_port": 0,
                    "dst_ip": "127.0.0.1",
                    "dst_port": receiver.getsockname()[1],
                    "payload_bytes": 64,
                    "payload_sizes_bytes": [90, 1200, 159],
                    "offsets_s": [0, 0.001, 0.002],
                }
            ],
        }
        traffic.run_sender(plan, tmp_path / "sender.jsonl")
        payloads = [receiver.recv(2048) for _ in range(3)]
    assert [len(payload) for payload in payloads] == [90, 1200, 159]
    assert [payload[: traffic.HEADER.size].hex() for payload in payloads] == [
        traffic.packet_id(7, 1, seq) for seq in (1, 2, 3)
    ]
    rows = [json.loads(line) for line in (tmp_path / "sender.jsonl").read_text().splitlines()]
    assert [row["size_bytes"] for row in rows] == [132, 1242, 201]


def test_demo_json_uses_the_same_profile_loader() -> None:
    assert (
        workload.load_profile(DEMO_PROFILE)["traffic_shapes"] == _demo_profile()["traffic_shapes"]
    )


def test_queue_rate_uses_actual_variable_frame_sizes() -> None:
    profile = _demo_profile()
    plan = {"flows": [{"offsets_s": [0, 0.1], "payload_sizes_bytes": [90, 1200]}]}
    assert workload.queue_rate_pps(profile, 10, plan) == 1819
    with pytest.raises(ValueError, match="plan"):
        workload.queue_rate_pps(profile, 10)


def test_join_validates_the_size_of_each_scheduled_packet(tmp_path: Path) -> None:
    flow = {
        "flow_id": "demo",
        "wire_id": 1,
        "truth": int(TrafficClass.HUMAN_INTERACTIVE),
        "src_ip": "10.0.0.1",
        "dst_ip": "10.0.0.2",
        "src_port": 33001,
        "dst_port": 33002,
        "payload_bytes": 64,
        "payload_sizes_bytes": [90, 1200, 159],
        "offsets_s": [0, 0.1, 0.2],
    }
    sent = [
        {
            "packet_id": traffic.packet_id(7, 1, seq),
            "flow_id": "demo",
            "sequence": seq,
            "sent_s": 100 + seq / 10,
            "size_bytes": size,
        }
        for seq, size in enumerate([132, 1242, 201], start=1)
    ]
    sender = tmp_path / "sender.jsonl"
    receiver = tmp_path / "receiver.jsonl"
    sender.write_text("".join(json.dumps(row) + "\n" for row in sent))
    receiver.write_text("")
    observations = workload.observations_from_logs(
        {"seed": 7, "flows": [flow]}, [sender], receiver, 100, 101
    )
    assert [packet["size_bytes"] for packet in observations["packets"]] == [132, 1242, 201]
    assert all(packet["received_s"] is None for packet in observations["packets"])
    sent[1]["size_bytes"] = 132
    sender.write_text("".join(json.dumps(row) + "\n" for row in sent))
    with pytest.raises(ValueError, match="size differs"):
        workload.observations_from_logs({"seed": 7, "flows": [flow]}, [sender], receiver, 100, 101)


@pytest.mark.parametrize("sizes", [[15], [1473], [True], [90.5], [], [90, 90]])
def test_variable_payload_sizes_are_validated_before_sending(sizes: list[object]) -> None:
    with pytest.raises(ValueError, match="payload sizes"):
        traffic.payload_sizes({"offsets_s": [0], "payload_sizes_bytes": sizes})


def test_integer_byte_distribution_includes_both_endpoints() -> None:
    from harness.demo_traffic import lane_schedules

    packets = next(
        lane_schedules(
            "human_video_call",
            _demo_profile()["traffic_shapes"]["human_video_call"],
            20,
            random.Random(3),
            10000,
        )
    )
    audio_sizes = {packet.payload_bytes for packet in packets if packet.payload_bytes < 1200}
    assert min(audio_sizes) == 90
    assert max(audio_sizes) == 159


@pytest.mark.parametrize("value", [1.5, True, "1000"])
def test_object_byte_cap_requires_an_integer(tmp_path: Path, value: object) -> None:
    profile = _demo_profile()
    profile["traffic_shapes"]["agent_mcp"]["max_object_bytes"] = value
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(profile))
    with pytest.raises(ValueError, match="shape"):
        workload.load_profile(path)


def test_mcp_cadence_and_segmentation_come_from_the_profile() -> None:
    from harness.demo_traffic import lane_schedules

    shape = copy.deepcopy(_demo_profile()["traffic_shapes"]["agent_mcp"])
    for key, value in (
        ("request_bytes", 600),
        ("response_bytes", 3000),
        ("server_delay_s", 0.03),
        ("think_s", 1.2),
    ):
        shape[key] = {"distribution": "uniform", "min": value, "max": value}
    shape["jitter_mean_s"] = 0
    shape["calls_per_step_mean"] = 0
    packets = next(lane_schedules("agent_mcp", shape, 3, random.Random(1), 100))
    assert [packet.payload_bytes for packet in packets[:4]] == [600, 1472, 1472, 56]
    assert packets[1].offset_s - packets[0].offset_s == pytest.approx(0.0306056)
    assert packets[4].offset_s - packets[3].offset_s == pytest.approx(1.2006056)


def test_bulk_storm_recurs_throughout_the_run() -> None:
    profile = workload.load_profile(DEMO_PROFILE)
    plan = workload.build_plan(profile, seed=1, share=30, burst="low", duration=30, link_mbps=20)
    for name in ("agent_ingest", "agent_crawler"):
        offsets = [
            offset
            for flow in plan["flows"]
            if flow["shape"] == name
            for offset in flow["offsets_s"]
        ]
        assert min(offsets) < 1
        assert max(offsets) > 25
        assert all(
            any(start <= offset < start + 5 for offset in offsets)
            for start in (0, 5, 10, 15, 20, 25)
        )


@pytest.mark.parametrize("share", [10, 30, 90])
def test_short_high_burst_cells_meet_the_class_load_tolerance(share: int) -> None:
    profile = workload.load_profile(DEMO_PROFILE)
    plan = workload.build_plan(profile, seed=1, share=share, burst="high", duration=1, link_mbps=10)
    for label, fraction in (
        (TrafficClass.HUMAN_INTERACTIVE, 1 - share / 100),
        (TrafficClass.AGENT_INTERACTIVE, share / 200),
        (TrafficClass.AGENT_BULK, share / 200),
    ):
        frame_bytes = sum(
            size + traffic.WIRE_BYTES
            for flow in plan["flows"]
            if flow["truth"] == label
            for size in flow["payload_sizes_bytes"]
        )
        assert frame_bytes * 8 / 0.8 == pytest.approx(12_000_000 * fraction, rel=0.02)


def test_unrepresentable_load_is_rejected_instead_of_omitting_classes() -> None:
    profile = _demo_profile()
    profile["drain_s"] = 0.99999
    with pytest.raises(ValueError, match="offered.load|budget|window"):
        workload.build_plan(profile, seed=1, share=10, burst="high", duration=1, link_mbps=10)


def test_budgeted_bulk_waves_keep_the_configured_gap() -> None:
    from harness.demo_traffic import lane_schedules

    shape = copy.deepcopy(_demo_profile()["traffic_shapes"]["agent_ingest"])
    for key, value in (
        ("request_bytes", 600),
        ("response_bytes", 600),
        ("server_delay_s", 0.03),
        ("wave_gap_s", 1.2),
    ):
        shape[key] = {"distribution": "uniform", "min": value, "max": value}
    shape.update(
        {
            "requests_per_flow": [1, 1],
            "parallel_flows": [2, 2],
            "jitter_mean_s": 0,
            "start_spread_s": 1e-10,
            "parse_gap_s": 1e-10,
        }
    )
    flows = list(lane_schedules("agent_ingest", shape, 3, random.Random(1), 100, wire_budget=5000))
    assert len(flows) == 6
    assert flows[2][0].offset_s - flows[0][-1].offset_s == pytest.approx(1.2006056, abs=1e-6)


def test_live_cell_runs_the_profile_named_on_the_command_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from contextlib import nullcontext
    from unittest.mock import Mock

    for name, value in {
        "AAN_SYSTEM": "fifo",
        "AAN_OUTPUT_DIR": str(tmp_path),
        "AAN_SEED": "1",
        "AAN_AGENT_SHARE_PCT": "30",
        "AAN_BURST_INTENSITY": "low",
        "AAN_DURATION_S": "30",
        "AAN_LINK_MBPS": "20",
        "AAN_OBSERVATIONS_PATH": str(tmp_path / "observations.json"),
        "AAN_TOPOLOGY": "choke_v1",
    }.items():
        monkeypatch.setenv(name, value)
    runtime = Mock()
    archive = Mock()
    monkeypatch.setattr("harness.runtime.RuntimeConfig", runtime)
    monkeypatch.setattr(workload, "archive_inputs", archive)
    monkeypatch.setattr(workload, "network_context", lambda *args: nullcontext("network"))
    monkeypatch.setattr(workload, "_run_network_traffic", Mock(return_value={"packets": []}))
    assert workload.main(["--profile", str(DEMO_PROFILE)]) == 0
    plan = json.loads((tmp_path / "workload-plan.json").read_text())
    shapes = json.loads(DEMO_PROFILE.read_text())["traffic_shapes"]
    assert {flow["shape"] for flow in plan["flows"]} == set(shapes)
    assert archive.call_args.args[1]["profile"] == DEMO_PROFILE
    expected = workload.queue_rate_pps(workload.load_profile(DEMO_PROFILE), 20, plan)
    assert runtime.call_args.kwargs["queue_rate_pps"] == expected
