"""Snapshot refinement race guards; controller decisions never forward or drop packets."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import Mock

import pytest

from common.compiled_tree import parse_compiled_tree
from common.contracts import (
    BYTES_DOWN_REGISTER,
    BYTES_UP_REGISTER,
    CM_SKETCH_COLUMNS,
    CM_SKETCH_REGISTERS,
    CM_SKETCH_ROWS,
    EPOCH_FLAG_REGISTER,
    FEATURE_BIT_WIDTHS,
    FEATURE_ORDER,
    FIRST_SIZE_COUNT,
    FIRST_SIZES_REGISTER,
    FIRST_TIMESTAMP_REGISTER,
    FLOW_CLASS_REGISTER,
    FLOW_KEY_REGISTER,
    IAT_EWMA_REGISTER,
    IAT_VARIANCE_EWMA_REGISTER,
    LAST_TIMESTAMP_REGISTER,
    PACKET_COUNT_REGISTER,
    PROTOCOL_METADATA_REGISTER,
    TrafficClass,
)
from common.feature_math import flow_tag, sketch_columns
from controller.punt_handler import FlowIdentity, FlowObservation
from controller.reclassifier import EpochScheduler, Reclassifier, RegisterSnapshot, collect_snapshot
from controller.switch_api import SwitchApiError

FLOW = FlowIdentity(0x0A000001, 0x0A000064, 6, 40000, 443)


def tree() -> object:
    """Two disjoint leaves distinguish IAT, with all other frozen fields unconstrained."""
    entries = []
    for low, high, label in ((0, 999, 2), (1000, (1 << 32) - 1, 1)):
        ranges = [[0, 0], *[[0, (1 << bits) - 1] for bits in FEATURE_BIT_WIDTHS]]
        ranges[1] = [low, high]
        entries.append(
            {
                "table": "tbl_tree_l0",
                "match_ranges": ranges,
                "action": "tree_leaf",
                "action_params": [label, 0],
                "priority": 1,
            }
        )
    return parse_compiled_tree(
        {"model_hash": "test", "feature_order": list(FEATURE_ORDER), "entries": entries}
    )


def snapshot(
    count: int = 6,
    iat: int = 100,
    tag: int | None = None,
    after: int | None = None,
    last: int | None = None,
) -> RegisterSnapshot:
    """A one-slot register snapshot with realistic flattened first-size and sketch arrays."""
    sketch = [0] * (CM_SKETCH_ROWS * CM_SKETCH_COLUMNS)
    for row, col in enumerate(sketch_columns(FLOW.client_ip)):
        sketch[row * CM_SKETCH_COLUMNS + col] = 7
    registers = {
        FLOW_KEY_REGISTER: (flow_tag(FLOW.key) if tag is None else tag,),
        FLOW_CLASS_REGISTER: (int(TrafficClass.UNKNOWN),),
        FIRST_TIMESTAMP_REGISTER: (1000,),
        LAST_TIMESTAMP_REGISTER: (count * 2000 if last is None else last,),
        IAT_EWMA_REGISTER: (iat,),
        IAT_VARIANCE_EWMA_REGISTER: (0,),
        BYTES_UP_REGISTER: (1200,),
        BYTES_DOWN_REGISTER: (3600,),
        FIRST_SIZES_REGISTER: (400,) * FIRST_SIZE_COUNT,
        PROTOCOL_METADATA_REGISTER: (0x0302,),
        EPOCH_FLAG_REGISTER: (0,),
        CM_SKETCH_REGISTERS[0]: tuple(sketch),
        CM_SKETCH_REGISTERS[1]: tuple(sketch),
    }
    return RegisterSnapshot((count,), registers, (count if after is None else after,))


def tracker(evidence: bool = False) -> Reclassifier:
    """Observe a flow whose slot ownership must still be checked against the switch."""
    classifier = Reclassifier(tree())
    classifier.observe(
        FlowObservation(
            0, FLOW, TrafficClass.UNKNOWN, TrafficClass.AGENT_INTERACTIVE if evidence else None
        )
    )
    return classifier


def test_features_use_common_fixed_point_math_and_frozen_order() -> None:
    assert snapshot().features(0, FLOW) == (100, 0, 6, 300, 58, 252, 7, 3, 2, 10)


def test_three_consecutive_sweeps_then_acknowledgment_makes_upsert_idempotent() -> None:
    classifier = tracker()
    assert classifier.sweep(snapshot(6)) == ()
    assert classifier.sweep(snapshot(7)) == ()
    (change,) = classifier.sweep(snapshot(8))
    assert change.traffic_class == TrafficClass.AGENT_INTERACTIVE
    assert change.operation == "upsert"
    assert classifier.sweep(snapshot(9)) == (change,)  # transport failed/not yet acknowledged
    classifier.acknowledge((change,))
    assert classifier.sweep(snapshot(10)) == ()


def test_flapping_and_torn_or_reset_snapshots_reset_candidate_streak() -> None:
    classifier = tracker()
    for count, iat in ((6, 100), (7, 1000), (8, 100), (9, 1000), (10, 100)):
        assert classifier.sweep(snapshot(count, iat)) == ()
    assert classifier.sweep(snapshot(11, after=20)) == ()
    assert classifier.sweep(snapshot(21)) == ()
    assert classifier.sweep(snapshot(22)) == ()
    assert classifier.sweep(snapshot(23))[0].traffic_class == TrafficClass.AGENT_INTERACTIVE
    classifier = tracker()
    assert classifier.sweep(snapshot(30, after=1)) == ()


def test_ownership_reuse_removes_sticky_evidence_without_touching_new_owner() -> None:
    classifier = tracker(evidence=True)
    changes = classifier.sweep(snapshot())
    assert changes[0].traffic_class == TrafficClass.AGENT_INTERACTIVE
    classifier.acknowledge(changes)
    (delete,) = classifier.sweep(snapshot(10, tag=42))
    assert delete.operation == "delete" and delete.flow == FLOW
    classifier.acknowledge((delete,))
    assert classifier.sweep(snapshot()) == ()


def test_evidence_precedence_and_three_idle_sweeps_expire_ttl() -> None:
    classifier = tracker(evidence=True)
    classifier.acknowledge(classifier.sweep(snapshot(6)))
    for count in (7, 8, 9):
        assert classifier.sweep(snapshot(count, iat=3000)) == ()
    assert classifier.sweep(snapshot(9)) == ()
    assert classifier.sweep(snapshot(9)) == ()
    (change,) = classifier.sweep(snapshot(9))
    assert change.operation == "delete"


def test_saturated_packet_counter_uses_timestamp_for_activity() -> None:
    classifier = tracker(evidence=True)
    classifier.acknowledge(classifier.sweep(snapshot((1 << 32) - 1, last=1000)))
    for last in (2000, 3000, 4000, 5000):
        assert classifier.sweep(snapshot((1 << 32) - 1, last=last)) == ()


def test_epoch_mismatch_and_malformed_registers_fail_open() -> None:
    classifier = tracker()
    assert classifier.sweep(replace(snapshot(), epoch_after=1)) == ()
    bad = dict(snapshot().registers)
    bad[PROTOCOL_METADATA_REGISTER] = (255,)
    assert classifier.sweep(replace(snapshot(), registers=bad)) == ()
    assert classifier.sweep(RegisterSnapshot((), {}, ())) == ()


def test_collect_snapshot_preserves_two_packet_count_reads_and_epoch_brackets() -> None:
    api = Mock()
    snap = snapshot()

    def read(names: list[str]) -> tuple[tuple[int, ...], ...]:
        first = True
        out = []
        for name in names:
            if name == PACKET_COUNT_REGISTER:
                out.append((6,) if first else (7,))
                first = False
            else:
                out.append(snap.registers[name])
        return tuple(out)

    api.read_registers_ordered.side_effect = read
    result = collect_snapshot(api)
    assert result.counts_before == (6,) and result.counts_after == (7,)
    api.read_registers_ordered.assert_called_once()


def test_epochs_clear_retired_before_reactivation_and_never_clear_active() -> None:
    api = Mock()
    api.read_register.return_value = (0,)
    scheduler = EpochScheduler(api)
    assert scheduler.tick(0)
    api.execute.assert_called_once_with(["register_write reg_epoch_flag 0 1"])
    assert not scheduler.tick(999999)
    api.read_register.return_value = (1,)
    assert scheduler.tick(1000000)
    assert api.execute.call_args.args[0] == [
        "register_reset reg_cm_sketch_0",
        "register_write reg_epoch_flag 0 0",
    ]
    assert not scheduler.tick(1000001)
    api.read_register.return_value = (0,)
    assert scheduler.tick(9000000)  # delayed ticks do one transition, never catch-up resets
    assert api.execute.call_args.args[0] == [
        "register_reset reg_cm_sketch_1",
        "register_write reg_epoch_flag 0 1",
    ]


def test_epoch_external_flip_or_failed_batch_does_not_clear_uncertain_epoch() -> None:
    api = Mock()
    api.read_register.return_value = (0,)
    scheduler = EpochScheduler(api)
    scheduler.tick(0)
    # Unexpected active flag: discard retirement knowledge; only flip on recovery.
    scheduler.tick(1000000)
    assert api.execute.call_args.args[0] == ["register_write reg_epoch_flag 0 1"]
    api.read_register.return_value = (1,)
    api.execute.side_effect = SwitchApiError("failed after partial batch")
    with pytest.raises(SwitchApiError):
        scheduler.tick(2000000)
    api.execute.side_effect = None
    api.read_register.return_value = (0,)
    scheduler.tick(3000000)
    assert api.execute.call_args.args[0] == ["register_write reg_epoch_flag 0 1"]
    with pytest.raises(ValueError):
        scheduler.tick(2999999)


def test_same_key_new_generation_cannot_inherit_evidence_or_streak() -> None:
    classifier = tracker(evidence=True)
    classifier.acknowledge(classifier.sweep(snapshot()))
    registers = dict(snapshot(7, iat=3000).registers)
    registers[FIRST_TIMESTAMP_REGISTER] = (2000,)
    (change,) = classifier.sweep(replace(snapshot(7, iat=3000), registers=registers))
    assert change.operation == "delete"
    classifier.acknowledge((change,))
    assert classifier.sweep(replace(snapshot(8, iat=3000), registers=registers)) == ()
    changes = classifier.sweep(replace(snapshot(9, iat=3000), registers=registers))
    assert changes[0].traffic_class == TrafficClass.HUMAN_INTERACTIVE


def test_new_punt_for_reused_slot_removes_old_desired_override() -> None:
    classifier = tracker(evidence=True)
    classifier.acknowledge(classifier.sweep(snapshot()))
    new_flow = FlowIdentity(FLOW.client_ip, FLOW.server_ip, FLOW.proto, 40001, 443)
    classifier.observe(FlowObservation(0, new_flow, TrafficClass.UNKNOWN))
    changes = classifier.sweep(snapshot(tag=flow_tag(new_flow.key)))
    assert len(changes) == 1 and changes[0].operation == "delete" and changes[0].flow == FLOW


def test_warmup_cannot_contribute_to_three_sweep_requirement() -> None:
    classifier = tracker()
    for count in (3, 4, 5, 6, 7):
        assert classifier.sweep(snapshot(count)) == ()
    assert classifier.sweep(snapshot(8))[0].traffic_class == TrafficClass.AGENT_INTERACTIVE


def test_bad_snapshot_breaks_prior_candidate_streak() -> None:
    classifier = tracker()
    classifier.sweep(snapshot(6))
    classifier.sweep(snapshot(7))
    assert classifier.sweep(replace(snapshot(8), epoch_after=1)) == ()
    assert classifier.sweep(snapshot(9)) == ()
    assert classifier.sweep(snapshot(10)) == ()
    assert classifier.sweep(snapshot(11))[0].operation == "upsert"


def test_snapshot_owns_immutable_copies() -> None:
    registers = dict(snapshot().registers)
    snap = replace(snapshot(), registers=registers)
    registers[IAT_EWMA_REGISTER] = (9999,)
    assert snap.features(0, FLOW)[0] == 100


def test_invalid_epoch_flag_refuses_to_write_any_register() -> None:
    api = Mock()
    api.read_register.return_value = (2,)
    with pytest.raises(SwitchApiError):
        EpochScheduler(api).tick(0)
    api.execute.assert_not_called()


def test_punt_from_live_slot_collider_cannot_erase_owner_evidence() -> None:
    classifier = tracker(evidence=True)
    classifier.acknowledge(classifier.sweep(snapshot()))
    collider = FlowIdentity(FLOW.client_ip, FLOW.server_ip, FLOW.proto, 40001, 443)
    classifier.observe(FlowObservation(0, collider, TrafficClass.UNKNOWN))
    # §7.1: the switch still owns this slot for FLOW; the collider cannot replace bookkeeping.
    assert classifier.sweep(snapshot(7)) == ()
    assert classifier.sweep(snapshot(8, iat=3000)) == ()
