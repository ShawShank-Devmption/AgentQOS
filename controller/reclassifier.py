"""Host-testable P3.11 snapshot refinement, acknowledgment and sketch epoch core.

Override changes are semantic intents: the frozen design does not specify the P4
flow-override action name/signature. No guessed commands are sent to that table.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from common.compiled_tree import CompiledTree, classify
from common.contracts import (
    BYTES_DOWN_REGISTER,
    BYTES_UP_REGISTER,
    CM_SKETCH_COLUMNS,
    CM_SKETCH_REGISTERS,
    CM_SKETCH_ROWS,
    EPOCH_FLAG_REGISTER,
    FEATURE_BIT_WIDTHS,
    FIRST_SIZE_COUNT,
    FIRST_SIZES_REGISTER,
    FIRST_TIMESTAMP_REGISTER,
    FLOW_CLASS_REGISTER,
    FLOW_KEY_REGISTER,
    FLOW_SLOTS,
    IAT_EWMA_REGISTER,
    IAT_VARIANCE_EWMA_REGISTER,
    IDLE_TIMEOUT_US,
    LAST_TIMESTAMP_REGISTER,
    PACKET_COUNT_REGISTER,
    PROTOCOL_METADATA_REGISTER,
    SKETCH_EPOCH_US,
    TIMESTAMP_BITS,
    WARMUP_PACKETS,
    TrafficClass,
)
from common.feature_math import (
    first8_size_bucket,
    flow_age_ms,
    mean_pkt_size_up,
    sketch_columns,
    unpack_proto_meta,
    updown_ratio_x100,
)
from controller.punt_handler import FlowIdentity, FlowObservation
from controller.switch_api import (
    SwitchApi,
    SwitchApiError,
    register_reset_command,
    register_write_command,
)

logger = logging.getLogger(__name__)
# Controller-local choices G9/G10 in docs/controller_design.md; these are not wire contracts.
_SWEEP_PERIOD_US = 2_000_000
_AGREEING_SWEEPS = 3
_TORN_PACKET_THRESHOLD = 8
_IDLE_SWEEPS = (IDLE_TIMEOUT_US + _SWEEP_PERIOD_US - 1) // _SWEEP_PERIOD_US
_SNAPSHOT_REGISTERS = (
    FLOW_KEY_REGISTER,
    FLOW_CLASS_REGISTER,
    FIRST_TIMESTAMP_REGISTER,
    LAST_TIMESTAMP_REGISTER,
    IAT_EWMA_REGISTER,
    IAT_VARIANCE_EWMA_REGISTER,
    BYTES_UP_REGISTER,
    BYTES_DOWN_REGISTER,
    FIRST_SIZES_REGISTER,
    PROTOCOL_METADATA_REGISTER,
    *CM_SKETCH_REGISTERS,
)


@dataclass(frozen=True)
class RegisterSnapshot:
    """Immutable bulk-read arrays bracketed by packet counts and sketch epoch flags."""

    counts_before: tuple[int, ...]
    registers: Mapping[str, tuple[int, ...]]
    counts_after: tuple[int, ...]
    epoch_after: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "counts_before", tuple(self.counts_before))
        object.__setattr__(self, "counts_after", tuple(self.counts_after))
        object.__setattr__(
            self,
            "registers",
            MappingProxyType({key: tuple(value) for key, value in self.registers.items()}),
        )

    def features(self, slot: int, flow: FlowIdentity) -> tuple[int, ...]:
        """Reconstruct FEATURE_ORDER with the same fixed-point functions as offline extraction.

        Raises:
            ValueError: If any register value or vector shape is malformed.
        """
        count = _cell(self.counts_before, slot, 32)
        first_ts = self.value(FIRST_TIMESTAMP_REGISTER, slot, TIMESTAMP_BITS)
        last_ts = self.value(LAST_TIMESTAMP_REGISTER, slot, TIMESTAMP_BITS)
        ext_count, alpn = unpack_proto_meta(self.value(PROTOCOL_METADATA_REGISTER, slot, 16))
        epoch = self.value(EPOCH_FLAG_REGISTER, 0, 1)
        sketch = self.registers.get(CM_SKETCH_REGISTERS[epoch], ())
        if len(sketch) != CM_SKETCH_ROWS * CM_SKETCH_COLUMNS:
            raise ValueError("malformed sketch array length")
        fanout = min(
            _cell(sketch, row * CM_SKETCH_COLUMNS + col, 16)
            for row, col in enumerate(sketch_columns(flow.client_ip))
        )
        sizes = self.registers.get(FIRST_SIZES_REGISTER, ())
        if len(sizes) < (slot + 1) * FIRST_SIZE_COUNT:
            raise ValueError("truncated first-size register")
        first_sizes = tuple(
            _cell(sizes, slot * FIRST_SIZE_COUNT + index, 16)
            for index in range(min(count, FIRST_SIZE_COUNT))
        )
        values = (
            self.value(IAT_EWMA_REGISTER, slot, 32),
            self.value(IAT_VARIANCE_EWMA_REGISTER, slot, 32),
            count,
            mean_pkt_size_up(self.value(BYTES_UP_REGISTER, slot, 32), count),
            updown_ratio_x100(
                self.value(BYTES_UP_REGISTER, slot, 32),
                self.value(BYTES_DOWN_REGISTER, slot, 32),
            ),
            first8_size_bucket(first_sizes),
            fanout,
            ext_count,
            int(alpn),
            flow_age_ms(last_ts, first_ts),
        )
        if any(not 0 <= value < 1 << bits for value, bits in zip(values, FEATURE_BIT_WIDTHS)):
            raise ValueError("feature outside frozen width")
        return values

    def value(self, name: str, index: int, bits: int) -> int:
        """Return one validated unsigned cell, rejecting missing/truncated state."""
        return _cell(self.registers.get(name, ()), index, bits)


@dataclass(frozen=True)
class OverrideChange:
    """Idempotent desired exact-flow operation, independent of an unresolved P4 action ABI."""

    operation: str
    flow: FlowIdentity
    traffic_class: TrafficClass | None


@dataclass
class _TrackedFlow:
    observation: FlowObservation
    activity: tuple[int, int] | None = None
    first_timestamp: int | None = None
    idle_sweeps: int = 0
    candidate: TrafficClass | None = None
    agreeing_sweeps: int = 0
    evidence: TrafficClass | None = None

    def reset_candidate(self) -> None:
        self.candidate = None
        self.agreeing_sweeps = 0


class Reclassifier:
    """Reconcile punt identities with switch state, producing retryable override intents."""

    def __init__(self, tree: CompiledTree) -> None:
        self._tree = tree
        self._flows: dict[int, _TrackedFlow] = {}
        self._pending: dict[int, FlowObservation] = {}
        self._desired: dict[FlowIdentity, TrafficClass] = {}
        self._installed: dict[FlowIdentity, TrafficClass] = {}

    @property
    def needs_snapshot(self) -> bool:
        """Return whether identity tracking or unacknowledged overrides require a sweep."""
        # §7.4/§7.5: avoid empty full-array reads, retaining pending ownership and write retries.
        return bool(self._flows or self._pending or self._desired or self._installed)

    def observe(self, observation: FlowObservation) -> None:
        """Remember a punt identity without trusting its slot until a subsequent sweep."""
        if not 0 <= observation.slot < FLOW_SLOTS:
            raise ValueError("flow slot outside register array")
        previous = self._flows.get(observation.slot)
        if previous is not None and previous.observation.flow == observation.flow:
            if observation.evidence_class is not None:
                previous.evidence = observation.evidence_class
            return
        if previous is not None:
            # §7.1: a clone from a live-slot collider must not evict the known owner's evidence.
            # Keep at most one provisional replacement per slot; loss only loses refinement.
            self._pending[observation.slot] = observation
            return
        self._flows[observation.slot] = _TrackedFlow(
            observation, evidence=observation.evidence_class
        )

    def sweep(self, snapshot: RegisterSnapshot) -> tuple[OverrideChange, ...]:
        """Process one two-second snapshot; return unacknowledged upserts/deletes.

        The caller applies intents through SwitchApi only after the override action ABI is
        approved, and acknowledges successful writes. Failed writes are retried next sweep.
        """
        try:
            epoch = snapshot.value(EPOCH_FLAG_REGISTER, 0, 1)
            if snapshot.epoch_after is not None and snapshot.epoch_after != epoch:
                raise ValueError("sketch epoch changed during snapshot")
        except ValueError as exc:
            logger.warning("Skipping unusable register snapshot: %s", exc)
            for state in self._flows.values():
                state.reset_candidate()
            return self._changes()
        for slot, state in tuple(self._flows.items()):
            try:
                pending = self._pending.get(slot)
                if pending is not None:
                    owner = snapshot.value(FLOW_KEY_REGISTER, slot, 32)
                    if owner == pending.flow.tag:
                        # §7.11: replace bookkeeping only once the switch confirms a new owner.
                        self._desired.pop(state.observation.flow, None)
                        state = _TrackedFlow(pending, evidence=pending.evidence_class)
                        self._flows[slot] = state
                    self._pending.pop(slot)
                self._process_slot(snapshot, slot, state)
            except ValueError as exc:
                logger.warning("Skipping malformed slot %s: %s", slot, exc)
                state.reset_candidate()
        return self._changes()

    def acknowledge(self, changes: Sequence[OverrideChange]) -> None:
        """Record only operations the caller has successfully applied to the switch.

        Acknowledge all successes even if a batch partially fails; CLI failure is not proof
        that no command ran. A restart clears tbl_flow_override via the startup policy.
        """
        for change in changes:
            if change.operation == "delete":
                self._installed.pop(change.flow, None)
            elif change.operation == "upsert" and change.traffic_class is not None:
                self._installed[change.flow] = change.traffic_class
            else:
                raise ValueError("invalid override operation")

    def _process_slot(self, snapshot: RegisterSnapshot, slot: int, state: _TrackedFlow) -> None:
        flow = state.observation.flow
        # §7.1/7.11: validate ownership before any evidence, classification, or aging action.
        if snapshot.value(FLOW_KEY_REGISTER, slot, 32) != flow.tag:
            self._forget(slot)
            return
        before = _cell(snapshot.counts_before, slot, 32)
        after = _cell(snapshot.counts_after, slot, 32)
        # §7.4: torn/reset views break the consecutive-sweep chain and never relabel.
        if after < before or after - before > _TORN_PACKET_THRESHOLD:
            state.reset_candidate()
            return
        first_ts = snapshot.value(FIRST_TIMESTAMP_REGISTER, slot, TIMESTAMP_BITS)
        last_ts = snapshot.value(LAST_TIMESTAMP_REGISTER, slot, TIMESTAMP_BITS)
        if state.first_timestamp is not None and state.first_timestamp != first_ts:
            # §7.11: same key reused after timeout still denotes a fresh flow generation.
            state.evidence = None
            state.activity = None
            state.idle_sweeps = 0
            state.reset_candidate()
            self._desired.pop(flow, None)
        state.first_timestamp = first_ts
        activity = (after, last_ts)
        state.idle_sweeps = state.idle_sweeps + 1 if state.activity == activity else 0
        state.activity = activity
        # §7.8/7.11: sticky evidence and behavioral overrides expire with the idle owner.
        if state.idle_sweeps >= _IDLE_SWEEPS:
            self._forget(slot)
            return
        if state.evidence is not None:
            self._desired[flow] = state.evidence
            state.reset_candidate()
            return
        if before < WARMUP_PACKETS:
            state.reset_candidate()
            return
        features = snapshot.features(slot, flow)
        decision = classify(self._tree, features).traffic_class
        current = self._desired.get(
            flow, TrafficClass(snapshot.value(FLOW_CLASS_REGISTER, slot, 2))
        )
        if decision == current:
            state.reset_candidate()
            return
        if state.candidate == decision:
            state.agreeing_sweeps += 1
        else:
            state.candidate = decision
            state.agreeing_sweeps = 1
        # §7.8: only three consecutive agreeing, usable snapshots may refine a flow class.
        if state.agreeing_sweeps >= _AGREEING_SWEEPS:
            self._desired[flow] = decision
            state.reset_candidate()

    def _forget(self, slot: int) -> None:
        state = self._flows.pop(slot)
        self._pending.pop(slot, None)
        self._desired.pop(state.observation.flow, None)

    def _changes(self) -> tuple[OverrideChange, ...]:
        changes = [
            OverrideChange("delete", flow, None)
            for flow in self._installed
            if flow not in self._desired
        ]
        changes.extend(
            OverrideChange("upsert", flow, label)
            for flow, label in self._desired.items()
            if self._installed.get(flow) != label
        )
        return tuple(sorted(changes, key=lambda change: (change.flow.key, change.operation)))


def collect_snapshot(api: SwitchApi) -> RegisterSnapshot:
    """Read every needed register in one CLI batch, retaining both consistency brackets."""
    # §7.4/7.10: count and epoch brackets survive duplicate register names in an ordered result.
    names = [
        PACKET_COUNT_REGISTER,
        EPOCH_FLAG_REGISTER,
        *_SNAPSHOT_REGISTERS,
        EPOCH_FLAG_REGISTER,
        PACKET_COUNT_REGISTER,
    ]
    arrays = api.read_registers_ordered(names)
    registers = dict(zip(names[1:-2], arrays[1:-2]))
    return RegisterSnapshot(arrays[0], registers, arrays[-1], _cell(arrays[-2], 0, 1))


class EpochScheduler:
    """Flip at most once per second; clear a retired sketch only after one settled interval."""

    def __init__(self, api: SwitchApi) -> None:
        self._api = api
        self._last_tick: int | None = None
        self._last_seen: int | None = None
        self._active: int | None = None
        self._retired: int | None = None

    def tick(self, now_us: int) -> bool:
        """Advance one epoch when due; timestamps must come from one monotonic clock.

        Raises:
            ValueError: If time moves backward or a timestamp is negative.
            SwitchApiError: If register reads/writes fail or the epoch flag is malformed.
        """
        if now_us < 0 or self._last_seen is not None and now_us < self._last_seen:
            raise ValueError("epoch scheduling requires nondecreasing monotonic microseconds")
        self._last_seen = now_us
        if self._last_tick is not None and now_us - self._last_tick < SKETCH_EPOCH_US:
            return False
        try:
            values = self._api.read_register(EPOCH_FLAG_REGISTER, 0)
            if len(values) != 1 or values[0] not in (0, 1):
                raise SwitchApiError("invalid sketch epoch flag")
            active = values[0]
            commands: list[str] = []
            # §7.10: clear only a known retired, settled array BEFORE making it active again.
            # Delayed ticks do one transition, never rapid catch-up flips/resets.
            if self._active == active and self._retired == 1 - active:
                commands.append(register_reset_command(CM_SKETCH_REGISTERS[self._retired]))
            commands.append(register_write_command(EPOCH_FLAG_REGISTER, 0, 1 - active))
            self._api.execute(commands)
        except SwitchApiError:
            # §7.10: a partially applied batch makes retirement knowledge unsafe; re-read later.
            self._active = None
            self._retired = None
            self._last_tick = now_us
            logger.exception("Sketch epoch update failed; discarding retirement knowledge")
            raise
        self._active, self._retired = 1 - active, active
        self._last_tick = now_us
        return True


def _cell(values: Sequence[int], index: int, bits: int) -> int:
    if not 0 <= index < len(values):
        raise ValueError("missing or truncated register cell")
    value = values[index]
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value < 1 << bits:
        raise ValueError(f"invalid unsigned {bits}-bit register cell")
    return value
