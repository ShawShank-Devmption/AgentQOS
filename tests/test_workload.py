"""Real UDP evidence and deterministic synthetic stress orchestration (P4.1)."""

from __future__ import annotations

import copy
import hashlib
import json
import socket
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from common.contracts import (
    CLASS_TREATMENTS,
    CPU_CONTROLLER_INTERFACE,
    SENDER_SWITCH_PORTS,
    TARGET_SWITCH_PORT,
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


def test_udp_app_limiter_is_explicitly_rejected_before_runtime(tmp_path: Path) -> None:
    profile = workload.load_profile(PROFILE)
    plan = workload.build_plan(profile, seed=1, share=30, burst="low", duration=2, link_mbps=20)
    with pytest.raises(ValueError, match="HTTP|UDP"):
        workload.network_context("app_limiter", None, profile, plan)
    assert not list(tmp_path.iterdir())


def test_ours_requires_an_explicit_compiled_tree_before_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = workload.load_profile(PROFILE)
    plan = workload.build_plan(profile, seed=1, share=30, burst="low", duration=2, link_mbps=20)
    monkeypatch.delenv("AAN_COMPILED_TREE", raising=False)
    with pytest.raises(ValueError, match="AAN_COMPILED_TREE"):
        workload.network_context("ours", None, profile, plan)


def test_controller_startup_failure_reaps_owned_child(tmp_path: Path) -> None:
    runtime = Mock(thrift_port=9090, link_mbps=20, output_dir=tmp_path)
    child = Mock()
    child.poll.return_value = 1
    with (
        patch("eval.workload.subprocess.Popen", return_value=child),
        pytest.raises(RuntimeError, match="controller exited"),
        workload.running_controller(runtime, Path("tree.json")),
    ):
        pytest.fail("unready controller yielded")
    child.wait.assert_called()


def test_controller_lifecycle_stops_owned_child_after_workload(tmp_path: Path) -> None:
    runtime = Mock(thrift_port=9090, link_mbps=20, output_dir=tmp_path)
    child = Mock()
    child.poll.return_value = None
    with (
        patch("eval.workload.subprocess.Popen", return_value=child) as spawn,
        patch("eval.workload._wait_for_controller"),
        workload.running_controller(runtime, Path("tree.json")) as running,
    ):
        assert running is child
    command = spawn.call_args.args[0]
    assert command[command.index("--cpu-interface") + 1] == CPU_CONTROLLER_INTERFACE
    assert [
        int(command[index + 1]) for index, value in enumerate(command) if value == "--upstream-port"
    ] == list(SENDER_SWITCH_PORTS)
    assert command[command.index("--downstream-port") + 1] == str(TARGET_SWITCH_PORT)
    child.terminate.assert_called_once()
    child.wait.assert_called()


def test_loopback_real_udp_sender_receiver_and_dscp(tmp_path: Path) -> None:
    if not hasattr(socket, "IP_RECVTOS"):
        pytest.skip("host Python lacks IP_RECVTOS; Linux measurement requires it")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    start = time.time() + 0.4
    actor = {
        "seed": 5,
        "start_s": start,
        "end_s": start + 0.3,
        "socket_buffer_bytes": 65536,
        "flows": [
            {
                "flow_id": "local",
                "wire_id": 1,
                "src_ip": "127.0.0.1",
                "src_port": 0,
                "dst_ip": "127.0.0.1",
                "dst_port": port,
                "payload_bytes": 64,
                "offsets_s": [0.0, 0.02, 0.04],
            }
        ],
    }
    target = {
        "bind_ip": "127.0.0.1",
        "ports": [port],
        "end_s": start + 0.3,
        "socket_buffer_bytes": 65536,
        "poll_s": 0.01,
    }
    for name, data in (("actor", actor), ("target", target)):
        (tmp_path / f"{name}.json").write_text(json.dumps(data))
    child = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "harness.traffic",
            "target",
            "--plan",
            str(tmp_path / "target.json"),
            "--log",
            str(tmp_path / "recv.jsonl"),
            "--ready",
            str(tmp_path / "ready.json"),
        ]
    )
    try:
        deadline = time.monotonic() + 2
        while not (tmp_path / "ready.json").exists():
            assert child.poll() is None
            assert time.monotonic() < deadline
            time.sleep(0.01)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "harness.traffic",
                "sender",
                "--plan",
                str(tmp_path / "actor.json"),
                "--log",
                str(tmp_path / "send.jsonl"),
            ],
            timeout=3,
            check=False,
        )
        assert result.returncode == 0
        assert child.wait(timeout=3) == 0
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
    sent = [json.loads(line) for line in (tmp_path / "send.jsonl").read_text().splitlines()]
    received = [json.loads(line) for line in (tmp_path / "recv.jsonl").read_text().splitlines()]
    assert len(sent) == len(received) == 3
    assert {r["packet_id"] for r in sent} == {r["packet_id"] for r in received}
    assert all(r["tos"] == 0 for r in received)
    assert all(r["size_bytes"] == 106 for r in received)
    assert "truth" not in received[0]
    original = (tmp_path / "send.jsonl").read_bytes()
    with pytest.raises(FileExistsError):
        traffic.run_sender(actor, tmp_path / "send.jsonl")
    assert (tmp_path / "send.jsonl").read_bytes() == original


class _ArrivalSocket:
    """One bound UDP socket whose single datagram arrives while recvmsg is waiting."""

    def __init__(self, *_args: object) -> None:
        self._pending = [bytes(traffic.HEADER.size)]

    def setsockopt(self, *_args: object) -> None:
        return None

    def bind(self, address: tuple[str, int]) -> None:
        self._address = address

    def settimeout(self, _timeout: float) -> None:
        return None

    def getsockname(self) -> tuple[str, int]:
        return self._address

    def recvmsg(self, *_args: object) -> tuple[bytes, list, int, tuple[str, int]]:
        if not self._pending:
            raise TimeoutError
        return self._pending.pop(), [], 0, ("10.0.0.1", 33001)

    def close(self) -> None:
        return None


def test_receiver_ignores_a_datagram_that_arrives_after_the_window_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Live BMv2 evidence: the loop checked the clock 0.1 ms before end_s, recvmsg then
    # returned a late datagram, and the whole run was rejected as corrupt evidence.
    clock = iter([99.0, 99.9999, 100.0001, 100.1, 100.2])
    monkeypatch.setattr(traffic.time, "time", lambda: next(clock))
    monkeypatch.setattr(traffic.socket, "socket", _ArrivalSocket)
    plan = {
        "bind_ip": "10.0.0.100",
        "ports": [33002],
        "end_s": 100.0,
        "socket_buffer_bytes": 65536,
        "poll_s": 0.01,
    }
    traffic.run_target(plan, tmp_path / "recv.jsonl", tmp_path / "ready.json")
    assert (tmp_path / "recv.jsonl").read_text() == ""


def test_agent_tool_calls_open_fresh_flows_while_the_human_call_stays_one_flow() -> None:
    # Design §2/§9: agents issue repeated parallel tool calls, so a burst keeps opening flows.
    profile = workload.load_profile(PROFILE)
    plan = workload.build_plan(profile, seed=3, share=80, burst="high", duration=4, link_mbps=10)
    human = [flow for flow in plan["flows"] if flow["truth"] == 1]
    agents = [flow for flow in plan["flows"] if flow["truth"] in (2, 3)]
    assert len(human) == 1
    assert human[0]["offsets_s"][-1] > 3.7
    assert all(f["offsets_s"][-1] - f["offsets_s"][0] < profile["agent_call_s"] for f in agents)
    # high burst = (1 interactive + 1 bulk) x 4 parallel lanes, each re-dialling per call.
    assert len(agents) >= 8 * (4 - profile["drain_s"]) / profile["agent_call_s"]
    ports = [flow["src_port"] for flow in plan["flows"]]
    assert len(ports) == len(set(ports))
