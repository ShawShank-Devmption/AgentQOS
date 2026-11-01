"""P4.4 deterministic input, collision invariants, and measured-evidence boundaries."""

import csv
import json
import shutil
from pathlib import Path
from unittest.mock import Mock

import pytest
from scapy.all import IP, PcapReader, PcapWriter

from common.contracts import (
    CLASS_TREATMENTS,
    COLLISION_COUNTER_REGISTER,
    FLOW_KEY_REGISTER,
    FLOW_SLOTS,
    LAST_TIMESTAMP_REGISTER,
    PACKET_COUNT_REGISTER,
    TrafficClass,
)
from common.feature_math import flow_slot, flow_tag
from eval.state_scaling import (
    analyze,
    capture_concurrency,
    generated_keys,
    load_prepared,
    load_profile,
    planned_collisions,
    prepare,
)
from eval.state_scaling_live import capture_command, read_rss_bytes, snapshot


def _profile(tmp_path: Path) -> Path:
    data = json.loads(Path("eval/configs/state_scaling.json").read_text())
    data["flow_counts"] = [4]
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(data))
    return path


def test_unique_100k_tuples_are_seeded_and_not_limited_by_source_ports() -> None:
    keys = generated_keys(100000, 1, 33002)
    assert len(set(keys)) == 100000
    assert keys[:5] == generated_keys(100000, 1, 33002)[:5]
    assert keys[:5] != generated_keys(100000, 2, 33002)[:5]
    with pytest.raises(ValueError):
        generated_keys(100000, True, 33002)


def test_live_collider_does_not_replace_first_owner() -> None:
    owners = {}
    for key in generated_keys(5000, 9, 33002):
        slot = flow_slot(key)
        if slot in owners and flow_tag(owners[slot]) != flow_tag(key):
            first = owners[slot]
            result = planned_collisions([first, key, first])
            assert result == {
                "occupied_slots": 1,
                "colliding_new_flows": 1,
                "ownership_tag_aliases": 0,
            }
            break
        owners[slot] = key
    else:
        pytest.fail("seeded collision fixture unexpectedly contains no collision")


def test_tag_alias_is_reported_separately(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("eval.state_scaling.flow_slot", lambda _: 0)
    monkeypatch.setattr("eval.state_scaling.flow_tag", lambda _: 1)
    assert planned_collisions([b"a", b"b"])["ownership_tag_aliases"] == 1


def test_preparation_is_deterministic_labelled_and_append_only(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    left, right = tmp_path / "left", tmp_path / "right"
    prepare(profile, 4, 1, left, 50)
    prepare(profile, 4, 1, right, 50)
    assert (left / "traffic.pcap").read_bytes() == (right / "traffic.pcap").read_bytes()
    assert (left / "labels.csv").read_bytes() == (right / "labels.csv").read_bytes()
    metadata = load_prepared(left)
    assert metadata["provenance"].endswith("not_measurement")
    with (left / "labels.csv").open() as stream:
        labels = list(csv.DictReader(stream))
    assert sorted(int(row["label"]) for row in labels) == [1, 1, 3, 3]
    with PcapReader(str(left / "traffic.pcap")) as packets:
        frames = list(packets)
    assert len(frames) == 24
    assert [float(packet.time) for packet in frames] == sorted(float(p.time) for p in frames)
    with pytest.raises(FileExistsError):
        prepare(profile, 4, 1, left, 50)
    (left / "labels.csv").write_text("changed")
    with pytest.raises(ValueError, match="hash"):
        load_prepared(left)


@pytest.mark.parametrize(
    "field,value",
    [
        ("offered_pps", 0),
        ("flow_counts", [4, 4]),
        ("packets_per_flow", 7),
        ("payload_bytes", 8),
        ("drain_s", float("nan")),
    ],
)
def test_invalid_profiles_fail_before_writing(tmp_path: Path, field: str, value: object) -> None:
    path = _profile(tmp_path)
    data = json.loads(path.read_text())
    data[field] = value
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_profile(path)


def _captures(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    prepared = tmp_path / "prepared"
    prepare(_profile(tmp_path), 4, 1, prepared, 50)
    ingress = tmp_path / "actual/traffic.pcap"
    ingress.parent.mkdir()
    shutil.copyfile(prepared / "traffic.pcap", ingress)
    with (prepared / "labels.csv").open() as stream:
        truths = {row["src_ip"]: int(row["label"]) for row in csv.DictReader(stream)}
    egress = tmp_path / "egress.pcap"
    with PcapReader(str(ingress)) as packets, PcapWriter(str(egress), linktype=1) as writer:
        for packet in packets:
            packet.time += 0.001
            packet[IP].tos = CLASS_TREATMENTS[TrafficClass(truths[packet[IP].src])].dscp << 2
            del packet[IP].chksum
            writer.write(packet)
    registers = {
        "before": {COLLISION_COUNTER_REGISTER: [0]},
        "after": {
            COLLISION_COUNTER_REGISTER: [0],
            FLOW_KEY_REGISTER: [0] * FLOW_SLOTS,
            PACKET_COUNT_REGISTER: [0] * FLOW_SLOTS,
            LAST_TIMESTAMP_REGISTER: [0] * FLOW_SLOTS,
        },
        "switch_rss_bytes": 123456,
    }
    for key in generated_keys(4, 1, 33002):
        slot = flow_slot(key)
        registers["after"][FLOW_KEY_REGISTER][slot] = flow_tag(key)
        registers["after"][PACKET_COUNT_REGISTER][slot] = 6
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(json.dumps(registers))
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text('{"register_arrays": [], "pipelines": []}')
    return prepared, ingress, egress, snapshot_path, pipeline


def test_analysis_uses_actual_counter_and_preserves_missing_checkpoints(tmp_path: Path) -> None:
    paths = _captures(tmp_path)
    report = analyze(*paths, 0, 2, tmp_path / "analysis")
    assert report["collision_packet_events"] == 0
    assert report["requested_concurrency_observed"] is True
    assert report["accuracy"]["6"]["accuracy_received"] == 1
    assert report["accuracy"]["16"] is None
    assert report["switch_rss_bytes"] == 123456
    assert report["occupied_slots_after"] == 4


def test_counter_reset_or_saturation_is_not_a_valid_result(tmp_path: Path) -> None:
    paths = _captures(tmp_path)
    path = paths[3]
    data = json.loads(path.read_text())
    data["before"][COLLISION_COUNTER_REGISTER] = [1]
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="fresh"):
        analyze(*paths, 0, 2, tmp_path / "analysis")


def test_actual_refresh_gaps_expose_aging_instead_of_claiming_concurrency(tmp_path: Path) -> None:
    prepared = tmp_path / "prepared"
    prepare(_profile(tmp_path), 4, 1, prepared, 50)
    slow = tmp_path / "slow.pcap"
    with (
        PcapReader(str(prepared / "traffic.pcap")) as packets,
        PcapWriter(str(slow), linktype=1) as out,
    ):
        for index, packet in enumerate(packets):
            packet.time = 1 + (index // 4) * 6 + (index % 4) * 0.01
            out.write(packet)
    result = capture_concurrency(slow)
    assert result["distinct_ingress_flows"] == 4
    assert result["refresh_within_idle_timeout"] is False


def test_torn_snapshot_is_rejected_and_capture_directions_are_explicit() -> None:
    api = Mock()
    api.read_registers_ordered.return_value = ((1,), (2,), (0,), (3,), (0,), (2,), (2,))
    with pytest.raises(RuntimeError, match="changed"):
        snapshot(api)
    command = capture_command("s1-eth1", "in", Path("traffic.pcap"))
    assert command[command.index("-Q") + 1] == "in"
    assert command[-1] == "traffic.pcap"


def test_actual_rss_is_distinct_from_logical_register_bits(tmp_path: Path) -> None:
    process = tmp_path / "123"
    process.mkdir()
    (process / "status").write_text("Name:\tsimple_switch\nVmRSS:\t2048 kB\n")
    assert read_rss_bytes(123, tmp_path) == 2 * 1024 * 1024


def test_plan_sweep_is_explicitly_not_measured_evidence(tmp_path: Path) -> None:
    from eval.state_scaling import prepare_sweep

    output = tmp_path / "plan"
    prepare_sweep(Path("eval/configs/state_scaling_1000.yaml"), _profile(tmp_path), output, True)
    data = json.loads((output / "plan.json").read_text())
    assert data["provenance"] == "analytical_hash_plan_not_switch_measurement"
    assert data["measured_accuracy"] is None
    assert len(data["rows"]) == 5
