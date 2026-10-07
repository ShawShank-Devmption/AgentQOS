# Person 2 (Dev B: Controller + ML) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver Dev B's eight work items (P2.4, P2.3/P3.2, P3.3, P3.6/P3.7, P3.11, P3.14, P4.5/P4.6, P1.2/P5.2). This plan gives full bite-sized steps for **Phase A** (items 1–2), which can be built now. Items 3–8 are a roadmap with fixed interfaces. Each roadmap item gets its own detailed plan once the item it depends on lands.

**Architecture:** One pure-stdlib, Python-3.9-safe module (`common/feature_math.py`) defines every fixed-point operation P4 performs. `ml/` (3.11) uses it to extract features offline. `controller/` (3.9 Ryu venv) uses it to rebuild features from register dumps. Controller I/O goes only through `controller/switch_api.py`, which batches `simple_switch_CLI` commands into a single session per operation. The compiled tree JSON (§4.5) has one loader/evaluator in `common/compiled_tree.py`. The equivalence gate and the reclassifier both use that loader/evaluator, so the gate checks the exact code the controller runs.

**Tech Stack:** Python 3.11 (ml, tests), Python 3.9 (controller runtime), pytest, scikit-learn, scapy, pandas, BMv2 `simple_switch_CLI`, ruff.

**Spec:** `docs/requirements.md`, `docs/design.md` (§4 frozen, §5.2, §6, §7, §8), `docs/tasks.md`, `docs/feature_arithmetic_design_gaps.md`.

## Global Constraints

- Python 3.11, PEP 8, 100-char lines, full type hints, Google docstrings on public functions only, `logging` (never `print`), lazy `%` log formatting, pathlib, dataclasses.
- `controller/**` and every `common/**` module the controller imports must run on **Python 3.9**. Use `from __future__ import annotations`. Don't use `match`, `X | Y` outside annotations, `zip(strict=)`, `dataclass(slots=/kw_only=)`, or parenthesized context managers. Runtime type aliases use `typing.Union`/`Tuple`.
- All shared constants come from `common/contracts.py`; never re-declare literals. Contract changes need a PR touching `common/contracts.py` and `docs/design.md` §4, with **2 approvals**.
- Allowed deps only: ryu, scikit-learn, pandas, matplotlib, pyyaml, scapy, pytest. Anything else needs team sign-off.
- Fail-open: no path may block or drop traffic because classification is uncertain. Punts and overrides are refinements.
- Code that touches flow state, slots, epochs, policy versions or the sweep cites the §7 item it preserves in a short comment.
- `make lint` and `make test` must be green before a task counts as done. One task ID per PR. The PR description lists the DoD items and how each was verified.
- P4 changes (generated classifier section, P3.6) are emitted in small increments. Someone compiles them with `p4c-bm2-ss` before the next step.

## Prerequisite (before Task 1)

The working tree has an uncommitted move of `design.md`/`requirements.md`/`tasks.md` into `docs/`. Commit that first, on its own and outside this plan, so the Phase A branch starts clean: `git checkout -b dev-b/phase-a`.

## DESIGN GAPs this plan resolves provisionally (raise at next sync)

| # | Gap | Provisional decision in this plan | Needs |
|---|---|---|---|
| G1 | `flow_age_ms` needs the flow's first timestamp; §4.2 has no register for it | Add `reg_first_ts` (**frozen-contract change, STOP until approved**) | 2 approvals |
| G2 | Shift-division, ×100, first-8 encoding, ALPN enum, ClientHello bounds | Exact definitions in Task 2 / `docs/feature_arithmetic.md` | Dev A agreement (P2.4) |
| G3 | Bidirectional slot needs a canonical key | Key = (client ip, server ip, proto, client port, server port); P4 picks the client side by ingress port, Python by flow initiator (same under `choke_v1`) | Dev A |
| G4 | Slot/tag/sketch hash algorithms unspecified | BMv2 `crc32` over the field list plus a salt byte (slot salt 0, tag salt 1, sketch salt = row). Must check that BMv2 crc32 equals `zlib.crc32` | Dev A, checked in P3.3 |
| G5 | "Expected" `policy_version` is never defined | Every controller start pushes the full policy idempotently, then writes version+1 | Team |
| G6 | Ryu's OpenFlow loop has no role with BMv2 Thrift | Controller stays a plain Python process (as at M1) | Team |
| G7 | Reclassifier "recomputes with the same sklearn model", but the controller venv has no sklearn | Evaluate `compiled_tree.json` via `common/compiled_tree.py`; the §7.9 gate proves it matches sklearn | Team |
| G8 | Sweep reads 5 arrays but the tree needs 10 features; slot→5-tuple can't be recovered from registers | Read every feature register; act only on slots whose 5-tuple was learned from a punt, validated by `flow_tag` | Team |
| G9 | Controller has no switch clock to compute `now − last_ts` | A slot is idle when `pkt_count` hasn't changed for `ceil(IDLE_TIMEOUT / T)` = 3 sweeps | Team |
| G10 | Torn-read "threshold" has no value | `TORN_READ_MAX_ADVANCE = 8` packets | Team |
| G11 | JA4/WBA overrides vs reclassifier overrides: which wins? | Evidence overrides stay until the flow ages out; the reclassifier only manages flows without evidence | Team |
| G12 | WBA needs Ed25519 (not stdlib); punts only carry TLS first packets | Request `cryptography` sign-off; Dev A also punts the first plaintext-HTTP payload packet | Team + Dev A |
| G13 | Evasion needs a think-time knob; the §4.6 config schema has none | **STOP**: propose optional `think_time_profile: none \| human_iat` | 2 approvals |
| G14 | BMv2 timestamps ≠ pcap timestamps, so timing can't be bit-exact on replay | Bit-exact check uses per-packet switch timestamps recorded by Dev A's PTF | Dev A |
| G15 | "±ε guard bands" undefined | Leaves with purity < 0.85 compile to `UNKNOWN` + punt reason 2 (§8 step 4); no other guard band | Team |
| G16 | Action signatures for `tbl_class_action`, tree tables, `tbl_punt_filter`; meter indexing | Contract constants in Task 1 | Dev A |
| G17 | Sketch epoch phase is controller-timed, so it can't be reproduced offline | Offline epochs start at the capture's first packet; fan-out bit-exactness is tested within a single epoch only | Team |

## Review Focus

1. **Negative EWMA steps.** A falling IAT sample must truncate toward zero like the unsigned P4 branch, not floor like Python's `>>` (`ewma_step(10, 3) == 10`, not 9). Test: Task 2.
2. **ClientHello split or malformed.** Modern Chrome ClientHellos often span two segments. Count only complete extensions in the first segment. Non-ClientHello or truncated-header payloads give `(0, NONE)`. Test: Task 2.
3. **CLI rejects a command but exits 0.** A `DUPLICATE_ENTRY`/`Invalid … operation`/`Unknown syntax` response must raise `SwitchApiError`. Test: Task 6.
4. **Controller dies mid-push, then restarts.** The second push must succeed with no duplicate-entry errors, leave the same table state, and raise the version exactly once. Test: Task 9.
5. **Controller code is imported under Python 3.9.** A 3.10-only construct must fail CI, not the VM demo. Check: Task 10.

---

## PHASE A — detailed tasks (items 1–2: P2.4, P2.3, P3.2)

### Task 0: P2.4 agreement session with Person 1 (no code)

**Files:** none (meeting outcome recorded in `docs/notebook.md` in Task 4)

- [ ] **Step 1: Walk Person 1 through the proposals.** Get a yes/no on each:
  - `reg_first_ts` register (G1); canonical key and direction by ingress port (G3); crc32+salt hashing (G4).
  - IAT: Δ = 48-bit wrapping difference clamped to 32 bits. Skip when Δ == 0. The first non-zero Δ seeds `iat_ewma = Δ` (detected as `iat_ewma == 0`). After that, `ewma_step` with truncate-toward-zero magnitude. The deviation uses the **pre-update** mean.
  - `mean_pkt_size_up = bytes_up >> floor_log2(pkt_count)`. `pkt_count` counts both directions. Result saturates at 16 bits.
  - `updown_ratio_x100 = (bytes_up·100) >> floor_log2(bytes_down)`, where ·100 = `(x<<6)+(x<<5)+(x<<2)`. If `bytes_down == 0` the result is 65535. Saturates at 16 bits.
  - `first8_size_bucket`: bit (7−i) is set when packet i ≥ 128 bytes (frame length, `standard_metadata.packet_length`).
  - `fanout_new_flows`: CM sketch counters are 16-bit saturating. The sketch is incremented with the client IP on claim. Read = minimum over rows of the active epoch.
  - `reg_proto_meta = (ext_count << 8) | alpn_class`. `AlpnClass` NONE=0, H1=1, H2=2, OTHER=3. Count complete extensions within both the first payload segment and the extensions block, up to 16. Only the first payload packet is parsed.
  - `flow_age_ms = Δ(now, first_ts) >> 10` (1.024 ms units).
  - Table contracts (G16):
    - `tbl_tree_l*` key = `(meta.tree_node exact 16b, 10 features range)`. Actions `tree_next(bit<16> node)` and `tree_leaf(bit<2> class, bit<8> punt_reason)`.
    - `tbl_class_action` key = class; action `set_class_action(dscp, queue_priority, meter_select, ecn_threshold_pkts)` with meter_select 0=none, 1=M_AI, 2=M_AB, and ECN threshold 0 = off.
    - `tbl_punt_filter` key = punt reason; action `punt`.
    - `M_AI`/`M_AB` are byte meters of size 2, indexed by `reg_congestion_flag` (0 normal, 1 protective).
- [ ] **Step 2: Record every change Person 1 requests.** Apply it to the Task 1 constants and Task 2 code before starting them. The hand-computed test values in Tasks 2–3 follow these definitions, so recompute them if a definition changes.

### Task 1: Contract PR for P2.4 + controller↔P4 signatures

**Files:**
- Modify: `common/contracts.py`
- Modify: `docs/design.md` (§4.2 register list, §4.3 note)
- Test: `tests/test_contracts.py`

**Interfaces:**
- Produces: `FIRST_TIMESTAMP_REGISTER`, `AlpnClass`, `TIMESTAMP_BITS`, `FIRST_SIZE_LARGE_BYTES`, `UPDOWN_RATIO_SCALE`, `FLOW_AGE_SHIFT`, `TLS_MAX_EXTENSIONS`, `CM_SKETCH_COUNTER_BITS`, `PROTO_META_EXT_COUNT_SHIFT`, `FLOW_KEY_SLOT_SALT`, `FLOW_KEY_TAG_SALT`, `TIMING_BURST_FEATURES`, `TREE_NODE_FIELD`, `TREE_NODE_BITS`, `TREE_KEY_FIELDS`, `TREE_NEXT_ACTION`, `TREE_LEAF_ACTION`, `CLASS_ACTION_NAME`, `PUNT_ACTION`, `METER_SELECT`, `PRESET_NORMAL`, `PRESET_PROTECTIVE`, `MeterShare`, `METER_PRESETS`, `METER_BURST_BYTES`.

- [ ] **Step 1: Write the failing tests** (append the test functions to `tests/test_contracts.py`; merge the names below into its existing top-of-file import, ruff `I`/`E402`):

```python
from common.contracts import (
    FIRST_TIMESTAMP_REGISTER,
    METER_AGENT_BULK,
    METER_AGENT_INTERACTIVE,
    METER_PRESETS,
    METER_SELECT,
    PRESET_NORMAL,
    PRESET_PROTECTIVE,
    TIMING_BURST_FEATURES,
    TREE_KEY_FIELDS,
    UPDOWN_RATIO_SCALE,
    AlpnClass,
)


def test_p24_feature_arithmetic_contracts() -> None:
    assert FIRST_TIMESTAMP_REGISTER in REGISTER_NAMES
    assert [alpn.value for alpn in AlpnClass] == [0, 1, 2, 3]
    assert (1 << 6) + (1 << 5) + (1 << 2) == UPDOWN_RATIO_SCALE
    assert set(TIMING_BURST_FEATURES) <= set(FEATURE_ORDER)
    assert TREE_KEY_FIELDS == ("tree_node", *FEATURE_ORDER)


def test_meter_select_covers_every_class_meter() -> None:
    assert {t.meter_name for t in CLASS_TREATMENTS.values()} == set(METER_SELECT)
    assert sorted(METER_SELECT.values()) == [0, 1, 2]


def test_meter_presets_are_two_rate_and_protective_caps_agents_at_30_pct() -> None:
    assert set(METER_PRESETS) == {PRESET_NORMAL, PRESET_PROTECTIVE}
    for shares in METER_PRESETS.values():
        assert set(shares) == {METER_AGENT_INTERACTIVE, METER_AGENT_BULK}
        assert all(0 < s.cir_pct < s.pir_pct <= 100 for s in shares.values())
    assert sum(s.cir_pct for s in METER_PRESETS[PRESET_PROTECTIVE].values()) == 30
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `.venv/bin/pytest tests/test_contracts.py -v`
Expected: FAIL with `ImportError: cannot import name 'FIRST_TIMESTAMP_REGISTER'`

- [ ] **Step 3: Implement.** In `common/contracts.py`, add `FIRST_TIMESTAMP_REGISTER: Final = "reg_first_ts"` after `LAST_TIMESTAMP_REGISTER`. Insert it into `REGISTER_NAMES` directly after `LAST_TIMESTAMP_REGISTER`. Then append:

```python
# --- P2.4 feature arithmetic (docs/feature_arithmetic.md) ---------------------------------


class AlpnClass(IntEnum):
    """Encoding of feature 8, `tls_alpn_class` (first ALPN protocol offered)."""

    NONE = 0
    H1 = 1
    H2 = 2
    OTHER = 3


TIMESTAMP_BITS: Final = 48
FIRST_SIZE_LARGE_BYTES: Final = 128
UPDOWN_RATIO_SCALE: Final = 100
FLOW_AGE_SHIFT: Final = 10
TLS_MAX_EXTENSIONS: Final = 16
CM_SKETCH_COUNTER_BITS: Final = 16
PROTO_META_EXT_COUNT_SHIFT: Final = 8
FLOW_KEY_SLOT_SALT: Final = 0
FLOW_KEY_TAG_SALT: Final = 1
TIMING_BURST_FEATURES: Final = ("iat_ewma_us", "iat_var_ewma", "fanout_new_flows")

# --- P4 table/action signatures the controller writes (design.md section 4.2) --------------
TREE_NODE_FIELD: Final = "tree_node"
TREE_NODE_BITS: Final = 16
TREE_KEY_FIELDS: Final = (TREE_NODE_FIELD, *FEATURE_ORDER)
TREE_NEXT_ACTION: Final = "tree_next"
TREE_LEAF_ACTION: Final = "tree_leaf"
CLASS_ACTION_NAME: Final = "set_class_action"
PUNT_ACTION: Final = "punt"
METER_SELECT: Final[Mapping[str | None, int]] = MappingProxyType(
    {None: 0, METER_AGENT_INTERACTIVE: 1, METER_AGENT_BULK: 2}
)

# --- Meter presets, indexed by reg_congestion_flag (design.md sections 5.5, 6.5) -------------
PRESET_NORMAL: Final = 0
PRESET_PROTECTIVE: Final = 1


@dataclass(frozen=True)
class MeterShare:
    """Two-rate meter rates as percentages of the bottleneck link."""

    cir_pct: int
    pir_pct: int


METER_PRESETS: Final[Mapping[int, Mapping[str, MeterShare]]] = MappingProxyType(
    {
        PRESET_NORMAL: MappingProxyType(
            {
                METER_AGENT_INTERACTIVE: MeterShare(cir_pct=50, pir_pct=80),
                METER_AGENT_BULK: MeterShare(cir_pct=30, pir_pct=60),
            }
        ),
        PRESET_PROTECTIVE: MappingProxyType(
            {
                METER_AGENT_INTERACTIVE: MeterShare(cir_pct=20, pir_pct=25),
                METER_AGENT_BULK: MeterShare(cir_pct=10, pir_pct=15),
            }
        ),
    }
)
METER_BURST_BYTES: Final = 15_000
```

Then update `test_p4_controller_names_match_design` only if it counts registers (it doesn't). In `docs/design.md`:
- §4.2: add `reg_first_ts` after `reg_last_ts`.
- §4.3: add the sentence "Exact fixed-point definitions: `docs/feature_arithmetic.md` (P2.4)."

- [ ] **Step 4: Run tests and lint**

Run: `.venv/bin/pytest tests/test_contracts.py -v && make lint`
Expected: PASS, lint clean.

- [ ] **Step 5: Commit, then open the contract PR (2 approvals before merge)**

```bash
git add common/contracts.py docs/design.md tests/test_contracts.py
git commit -m "P2.4: contracts for feature arithmetic, table signatures, meter presets"
```

### Task 2: `common/feature_math.py` — fixed-point primitives

**Files:**
- Create: `common/feature_math.py`
- Test: `tests/test_feature_math.py`

**Interfaces:**
- Consumes: Task 1 constants.
- Produces: `saturate(value, bits) -> int`, `floor_log2(value) -> int`, `timestamp_delta_us(now_us, earlier_us) -> int`, `ewma_step(current, sample) -> int`, `iat_update(iat_ewma, iat_var_ewma, delta_us) -> tuple[int, int]`, `mean_pkt_size_up(bytes_up, pkt_count) -> int`, `updown_ratio_x100(bytes_up, bytes_down) -> int`, `first8_size_bucket(first_sizes) -> int`, `flow_age_ms(now_us, first_us) -> int`, `pack_proto_meta(ext_count, alpn) -> int`, `unpack_proto_meta(value) -> tuple[int, AlpnClass]`, `parse_client_hello_meta(payload) -> tuple[int, AlpnClass]`, `flow_key_bytes(client_ip, server_ip, proto, client_port, server_port) -> bytes`, `flow_slot(key) -> int`, `flow_tag(key) -> int`, `sketch_columns(src_ip) -> tuple[int, ...]`.

- [ ] **Step 1: Write the failing tests** — `tests/test_feature_math.py`:

```python
"""Hand-computed fixed-point cases for the P4 feature arithmetic mirror (P2.4, §7.3, §7.9)."""

import pytest

from common.contracts import CM_SKETCH_COLUMNS, CM_SKETCH_ROWS, FLOW_SLOTS, AlpnClass
from common.feature_math import (
    ewma_step,
    first8_size_bucket,
    floor_log2,
    flow_age_ms,
    flow_key_bytes,
    flow_slot,
    flow_tag,
    iat_update,
    mean_pkt_size_up,
    pack_proto_meta,
    parse_client_hello_meta,
    saturate,
    sketch_columns,
    timestamp_delta_us,
    unpack_proto_meta,
    updown_ratio_x100,
)


def _client_hello(extensions: list[tuple[int, bytes]]) -> bytes:
    ext_blob = b"".join(
        t.to_bytes(2, "big") + len(body).to_bytes(2, "big") + body for t, body in extensions
    )
    body = (
        b"\x03\x03"
        + bytes(32)
        + b"\x00"
        + b"\x00\x02\x13\x01"
        + b"\x01\x00"
        + len(ext_blob).to_bytes(2, "big")
        + ext_blob
    )
    handshake = b"\x01" + len(body).to_bytes(3, "big") + body
    return b"\x16\x03\x01" + len(handshake).to_bytes(2, "big") + handshake


def _alpn(*protocols: bytes) -> bytes:
    names = b"".join(len(p).to_bytes(1, "big") + p for p in protocols)
    return len(names).to_bytes(2, "big") + names


def test_saturate_clamps_and_rejects_negative() -> None:
    assert saturate(70_000, 16) == 65_535
    assert saturate(5, 16) == 5
    with pytest.raises(ValueError):
        saturate(-1, 16)


def test_floor_log2() -> None:
    assert [floor_log2(v) for v in (1, 6, 8, 3_900)] == [0, 2, 3, 11]
    with pytest.raises(ValueError):
        floor_log2(0)


def test_timestamp_delta_wraps_48_bits_and_clamps_32() -> None:
    assert timestamp_delta_us(2_000, 500) == 1_500
    assert timestamp_delta_us(5, (1 << 48) - 5) == 10  # §7.3 wraparound
    assert timestamp_delta_us(1 << 40, 0) == (1 << 32) - 1


def test_ewma_step_truncates_toward_zero_like_unsigned_p4() -> None:
    assert ewma_step(0, 80) == 10
    assert ewma_step(100, 20) == 90
    assert ewma_step(10, 3) == 10  # Python floor shift would give 9


def test_iat_update_uses_pre_update_mean_for_deviation() -> None:
    assert iat_update(100_000, 0, 20_000) == (90_000, 10_000)
    assert iat_update(90_000, 10_000, 350_000) == (122_500, 41_250)


def test_mean_pkt_size_up() -> None:
    assert mean_pkt_size_up(1_200, 6) == 300
    assert mean_pkt_size_up(0, 0) == 0
    assert mean_pkt_size_up((1 << 32) - 1, 1) == 65_535


def test_updown_ratio_x100() -> None:
    assert updown_ratio_x100(1_200, 3_600) == 58
    assert updown_ratio_x100(3_200, 9_600) == 39
    assert updown_ratio_x100(5, 0) == 65_535  # §7.14 asymmetric visibility caps


def test_first8_size_bucket_first_packet_is_msb() -> None:
    assert first8_size_bucket([400, 1_200, 400, 1_200, 400, 1_200]) == 0b1111_1100
    assert first8_size_bucket([74, 1_500, 66, 900, 66, 1_500]) == 0b0101_0100
    assert first8_size_bucket([128]) == 0b1000_0000
    assert first8_size_bucket([]) == 0
    with pytest.raises(ValueError):
        first8_size_bucket([0] * 9)


def test_flow_age_is_microseconds_shifted_by_ten() -> None:
    assert flow_age_ms(10_000, 0) == 9
    assert flow_age_ms(625_000, 0) == 610


def test_proto_meta_round_trip() -> None:
    assert pack_proto_meta(3, AlpnClass.H2) == 0x0302
    assert unpack_proto_meta(0x0302) == (3, AlpnClass.H2)


@pytest.mark.parametrize(
    ("alpn_body", "expected"),
    [
        (_alpn(b"h2", b"http/1.1"), AlpnClass.H2),
        (_alpn(b"http/1.1"), AlpnClass.H1),
        (_alpn(b"h3"), AlpnClass.OTHER),
    ],
)
def test_client_hello_alpn_class(alpn_body: bytes, expected: AlpnClass) -> None:
    payload = _client_hello([(0x0000, b"\x00"), (0x0010, alpn_body), (0x002B, b"\x02\x03\x04")])
    assert parse_client_hello_meta(payload) == (3, expected)


def test_client_hello_without_alpn() -> None:
    assert parse_client_hello_meta(_client_hello([(0x0000, b"")])) == (1, AlpnClass.NONE)


def test_client_hello_counts_at_most_sixteen_extensions() -> None:
    payload = _client_hello([(0x0100 + i, b"") for i in range(20)])
    assert parse_client_hello_meta(payload) == (16, AlpnClass.NONE)


def test_client_hello_split_across_segments_counts_complete_extensions_only() -> None:
    payload = _client_hello([(0x0000, b"abcd"), (0x0010, _alpn(b"h2"))])
    assert parse_client_hello_meta(payload[:-2]) == (1, AlpnClass.NONE)


@pytest.mark.parametrize(
    "payload",
    [b"", b"GET / HTTP/1.1\r\n", b"\x16\x03\x01\x00\x05\x02", _client_hello([])[:20]],
)
def test_non_client_hello_payloads_fail_open(payload: bytes) -> None:
    assert parse_client_hello_meta(payload) == (0, AlpnClass.NONE)  # §7.15


def test_flow_key_layout_and_hash_ranges() -> None:
    key = flow_key_bytes(0x0A00_0001, 0x0A00_0002, 6, 40_000, 443)
    assert key == bytes.fromhex("0a000001" "0a000002" "06" "9c40" "01bb")
    assert 0 <= flow_slot(key) < FLOW_SLOTS
    assert 0 <= flow_tag(key) < 1 << 32
    assert flow_slot(key) == flow_slot(bytes(key))  # deterministic
    columns = sketch_columns(0x0A00_0001)
    assert len(columns) == CM_SKETCH_ROWS
    assert all(0 <= c < CM_SKETCH_COLUMNS for c in columns)
```

- [ ] **Step 2: Run and confirm failure**

Run: `.venv/bin/pytest tests/test_feature_math.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'common.feature_math'`

- [ ] **Step 3: Implement** — `common/feature_math.py`:

```python
"""Python mirror of the P4 fixed-point feature arithmetic (design.md sections 4.3, 7.3, 7.9).

Each function reproduces what p4src/features.p4 computes, bit for bit, as defined in
docs/feature_arithmetic.md. Pure stdlib and Python 3.9 safe: imported by ml/ and controller/.
"""

from __future__ import annotations

import zlib
from collections.abc import Sequence

from common.contracts import (
    CM_SKETCH_COLUMNS,
    CM_SKETCH_ROWS,
    EWMA_SHIFT,
    FIRST_SIZE_COUNT,
    FIRST_SIZE_LARGE_BYTES,
    FLOW_AGE_SHIFT,
    FLOW_KEY_SLOT_SALT,
    FLOW_KEY_TAG_SALT,
    FLOW_SLOTS,
    PROTO_META_EXT_COUNT_SHIFT,
    TIMESTAMP_BITS,
    TLS_MAX_EXTENSIONS,
    UPDOWN_RATIO_SCALE,
    AlpnClass,
)

_ALPN_EXTENSION_TYPE = 0x0010
_TLS_RECORD_HEADER_BYTES = 5
_HANDSHAKE_HEADER_BYTES = 4
_CLIENT_HELLO_TYPE = 0x01
_VERSION_AND_RANDOM_BYTES = 2 + 32


def saturate(value: int, bits: int) -> int:
    """Clamp a non-negative value to an unsigned `bits`-wide field (section 7.3)."""
    if value < 0:
        raise ValueError(f"fixed-point values are unsigned, got {value}")
    return min(value, (1 << bits) - 1)


def floor_log2(value: int) -> int:
    """Return the highest set bit index: the P4 shift that stands in for `/ value`."""
    if value <= 0:
        raise ValueError(f"floor_log2 requires a positive value, got {value}")
    return value.bit_length() - 1


def timestamp_delta_us(now_us: int, earlier_us: int) -> int:
    """Return the 48-bit wrapping difference clamped to 32 bits (section 7.3)."""
    return saturate((now_us - earlier_us) % (1 << TIMESTAMP_BITS), 32)


def ewma_step(current: int, sample: int) -> int:
    """Apply `current += (sample - current) >> EWMA_SHIFT` on unsigned registers.

    P4 shifts the magnitude, then applies the sign, which truncates toward zero; Python's `>>`
    on a negative difference floors instead, hence the two branches.
    """
    if sample >= current:
        return current + ((sample - current) >> EWMA_SHIFT)
    return current - ((current - sample) >> EWMA_SHIFT)


def iat_update(iat_ewma: int, iat_var_ewma: int, delta_us: int) -> tuple[int, int]:
    """Update the IAT mean and mean-absolute-deviation EWMAs for one non-zero gap.

    The deviation is taken against the pre-update mean so P4 reads each register once.
    """
    deviation = abs(delta_us - iat_ewma)
    return ewma_step(iat_ewma, delta_us), ewma_step(iat_var_ewma, deviation)


def mean_pkt_size_up(bytes_up: int, pkt_count: int) -> int:
    """Feature 3: `bytes_up >> floor_log2(pkt_count)`, saturated to 16 bits.

    `pkt_count` counts both directions and the shift rounds the divisor down to a power of
    two, so the value over-reads the true mean by less than 2x.
    """
    if pkt_count == 0:
        return 0
    return saturate(bytes_up >> floor_log2(pkt_count), 16)


def updown_ratio_x100(bytes_up: int, bytes_down: int) -> int:
    """Feature 4: `(bytes_up * 100) >> floor_log2(bytes_down)`, saturated to 16 bits.

    P4 computes `* 100` as `(x << 6) + (x << 5) + (x << 2)`. No downstream bytes (including
    one-way visibility, section 7.14) reads as the cap.
    """
    if bytes_down == 0:
        return (1 << 16) - 1
    return saturate((bytes_up * UPDOWN_RATIO_SCALE) >> floor_log2(bytes_down), 16)


def first8_size_bucket(first_sizes: Sequence[int]) -> int:
    """Feature 5: bit (7 - i) is set when packet i is at least FIRST_SIZE_LARGE_BYTES.

    The first packet is the most significant bit, so tree splits compare early packets first.
    """
    if len(first_sizes) > FIRST_SIZE_COUNT:
        raise ValueError(f"at most {FIRST_SIZE_COUNT} first sizes, got {len(first_sizes)}")
    bucket = 0
    for index, size in enumerate(first_sizes):
        if size >= FIRST_SIZE_LARGE_BYTES:
            bucket |= 1 << (FIRST_SIZE_COUNT - 1 - index)
    return bucket


def flow_age_ms(now_us: int, first_us: int) -> int:
    """Feature 9: flow age in 1.024 ms units (`delta_us >> 10`)."""
    return timestamp_delta_us(now_us, first_us) >> FLOW_AGE_SHIFT


def pack_proto_meta(ext_count: int, alpn: AlpnClass) -> int:
    """Pack features 7-8 the way reg_proto_meta stores them."""
    return (ext_count << PROTO_META_EXT_COUNT_SHIFT) | int(alpn)


def unpack_proto_meta(value: int) -> tuple[int, AlpnClass]:
    """Unpack reg_proto_meta into (tls_ext_count, tls_alpn_class)."""
    return (value >> PROTO_META_EXT_COUNT_SHIFT) & 0xFF, AlpnClass(value & 0xFF)


def parse_client_hello_meta(payload: bytes) -> tuple[int, AlpnClass]:
    """Features 7-8 from a flow's first payload segment, mirroring the bounded P4 parse.

    Counts extensions whose header and body lie inside both this segment and the extensions
    block, stopping at TLS_MAX_EXTENSIONS. A payload that is not a ClientHello parseable up to
    its extensions block yields the fail-open value (0, NONE) (section 7.15).
    """
    fail_open = (0, AlpnClass.NONE)
    if (
        payload[:2] != b"\x16\x03"
        or len(payload) <= _TLS_RECORD_HEADER_BYTES
        or payload[_TLS_RECORD_HEADER_BYTES] != _CLIENT_HELLO_TYPE
    ):
        return fail_open
    position = _TLS_RECORD_HEADER_BYTES + _HANDSHAKE_HEADER_BYTES + _VERSION_AND_RANDOM_BYTES
    for length_bytes in (1, 2, 1):  # session id, cipher suites, compression methods
        position = _skip_vector(payload, position, length_bytes)
        if position < 0:
            return fail_open
    if position + 2 > len(payload):
        return fail_open
    declared = int.from_bytes(payload[position : position + 2], "big")
    block_end = min(len(payload), position + 2 + declared)
    position += 2
    count = 0
    alpn = AlpnClass.NONE
    while count < TLS_MAX_EXTENSIONS and position + 4 <= block_end:
        ext_type = int.from_bytes(payload[position : position + 2], "big")
        body_end = position + 4 + int.from_bytes(payload[position + 2 : position + 4], "big")
        if body_end > block_end:
            break
        if ext_type == _ALPN_EXTENSION_TYPE:
            alpn = _alpn_class(payload[position + 4 : body_end])
        count += 1
        position = body_end
    return count, alpn


def flow_key_bytes(
    client_ip: int, server_ip: int, proto: int, client_port: int, server_port: int
) -> bytes:
    """Canonical 104-bit flow key, client side first, as P4 orders it by ingress port."""
    return (
        client_ip.to_bytes(4, "big")
        + server_ip.to_bytes(4, "big")
        + proto.to_bytes(1, "big")
        + client_port.to_bytes(2, "big")
        + server_port.to_bytes(2, "big")
    )


def flow_slot(key: bytes) -> int:
    """Register index: BMv2 `hash(crc32, {key, 8w0}) % FLOW_SLOTS`."""
    return zlib.crc32(key + bytes([FLOW_KEY_SLOT_SALT])) % FLOW_SLOTS


def flow_tag(key: bytes) -> int:
    """Slot ownership tag stored in reg_flow_key: BMv2 `hash(crc32, {key, 8w1})` (7.1)."""
    return zlib.crc32(key + bytes([FLOW_KEY_TAG_SALT]))


def sketch_columns(src_ip: int) -> tuple[int, ...]:
    """CM-sketch column for each row: BMv2 `hash(crc32, {src_ip, 8w row}) % columns`."""
    source = src_ip.to_bytes(4, "big")
    return tuple(
        zlib.crc32(source + bytes([row])) % CM_SKETCH_COLUMNS for row in range(CM_SKETCH_ROWS)
    )


def _skip_vector(data: bytes, position: int, length_bytes: int) -> int:
    """Skip a TLS length-prefixed vector; -1 when it runs past the segment."""
    header_end = position + length_bytes
    if header_end > len(data):
        return -1
    end = header_end + int.from_bytes(data[position:header_end], "big")
    return end if end <= len(data) else -1


def _alpn_class(body: bytes) -> AlpnClass:
    """Classify the first protocol of an ALPN extension body (RFC 7301)."""
    if len(body) < 3:
        return AlpnClass.OTHER
    name = body[3 : 3 + body[2]]
    if name == b"http/1.1":
        return AlpnClass.H1
    if name == b"h2":
        return AlpnClass.H2
    return AlpnClass.OTHER
```

- [ ] **Step 4: Run tests and lint**

Run: `.venv/bin/pytest tests/test_feature_math.py -v && make lint`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add common/feature_math.py tests/test_feature_math.py
git commit -m "P2.4: fixed-point feature arithmetic reference"
```

### Task 3: `ml/extract_features.py` core — per-flow state machine and checkpoints

**Files:**
- Create: `ml/extract_features.py`
- Test: `tests/test_extract_features.py`

**Interfaces:**
- Consumes: Task 2 functions.
- Produces: `PacketObservation(timestamp_us, size_bytes, upstream, payload=b"")`, `FlowState` (mutable mirror of one slot's registers), `update_flow(state, packet) -> None`, `feature_vector(state, fanout_new_flows) -> tuple[int, ...]`, `checkpoint_vectors(packets, fanout_new_flows) -> dict[int, tuple[int, ...]]`. P3.3 adds the pcap/sketch layer on top of these.

- [ ] **Step 1: Write the failing tests** — `tests/test_extract_features.py`:

```python
"""Per-flow feature stage mirror: hand-checked checkpoints and section 7 edge cases."""

from common.contracts import AlpnClass
from ml.extract_features import (
    FlowState,
    PacketObservation,
    checkpoint_vectors,
    update_flow,
)
from tests.test_feature_math import _alpn, _client_hello


def _agent_flow() -> list[PacketObservation]:
    return [
        PacketObservation(1_000_000 + 2_000 * i, 400 if i % 2 == 0 else 1_200, i % 2 == 0)
        for i in range(64)
    ]


def test_agent_constant_pacing_checkpoints() -> None:
    vectors = checkpoint_vectors(_agent_flow(), fanout_new_flows=1)
    assert vectors[6] == (2_000, 0, 6, 300, 58, 252, 1, 0, 0, 9)
    assert vectors[16] == (2_000, 0, 16, 200, 39, 255, 1, 0, 0, 29)
    assert vectors[64] == (2_000, 0, 64, 200, 39, 255, 1, 0, 0, 123)


def test_human_variable_pacing_six_packets() -> None:
    gaps = [0, 100_000, 20_000, 350_000, 5_000, 150_000]
    sizes = [74, 1_500, 66, 900, 66, 1_500]
    timestamp = 0
    packets = []
    for index, (gap, size) in enumerate(zip(gaps, sizes, strict=True)):
        timestamp += gap
        packets.append(PacketObservation(timestamp, size, index % 2 == 0))
    vectors = checkpoint_vectors(packets, fanout_new_flows=1)
    assert vectors == {6: (113_086, 49_707, 6, 51, 10, 84, 1, 0, 0, 610)}


def test_first_gap_seeds_mean_and_zero_gap_is_skipped() -> None:
    state = FlowState()
    for timestamp in (0, 0, 1_000):  # §7.12
        update_flow(state, PacketObservation(timestamp, 100, True))
    assert (state.iat_ewma, state.iat_var_ewma, state.pkt_count) == (1_000, 0, 3)


def test_byte_counters_saturate() -> None:
    state = FlowState(pkt_count=1, bytes_up=(1 << 32) - 10)
    update_flow(state, PacketObservation(10, 100, True))
    assert state.bytes_up == (1 << 32) - 1  # §7.3


def test_only_first_payload_packet_is_parsed_for_tls() -> None:
    hello = _client_hello([(0x0010, _alpn(b"h2"))])
    state = FlowState()
    update_flow(state, PacketObservation(0, 74, True))
    update_flow(state, PacketObservation(10, 66 + len(hello), True, hello))
    update_flow(state, PacketObservation(20, 66 + len(hello), True, _client_hello([])))
    assert (state.tls_ext_count, state.tls_alpn_class) == (1, AlpnClass.H2)


def test_non_tls_first_payload_locks_tls_features_to_fail_open() -> None:
    state = FlowState()
    update_flow(state, PacketObservation(0, 200, True, b"GET / HTTP/1.1\r\n"))
    update_flow(state, PacketObservation(10, 300, True, _client_hello([(0x0010, _alpn(b"h2"))])))
    assert (state.tls_ext_count, state.tls_alpn_class) == (0, AlpnClass.NONE)


def test_short_flow_has_no_checkpoints() -> None:
    assert checkpoint_vectors(_agent_flow()[:5], fanout_new_flows=1) == {}
```

- [ ] **Step 2: Run and confirm failure**

Run: `.venv/bin/pytest tests/test_extract_features.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ml.extract_features'`

- [ ] **Step 3: Implement** — `ml/extract_features.py`:

```python
"""Per-flow feature extraction mirroring the P4 feature stage (design.md 5.2, 8.2; P2.4/P3.3)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from common.contracts import FEATURE_CHECKPOINTS, FIRST_SIZE_COUNT, AlpnClass
from common.feature_math import (
    first8_size_bucket,
    flow_age_ms,
    iat_update,
    mean_pkt_size_up,
    parse_client_hello_meta,
    saturate,
    timestamp_delta_us,
    updown_ratio_x100,
)


@dataclass(frozen=True)
class PacketObservation:
    """One packet of a flow as the switch sees it."""

    timestamp_us: int
    size_bytes: int
    upstream: bool
    payload: bytes = b""


@dataclass
class FlowState:
    """Mirror of one flow slot's registers after the latest packet."""

    first_ts_us: int = 0
    last_ts_us: int = 0
    iat_ewma: int = 0
    iat_var_ewma: int = 0
    pkt_count: int = 0
    bytes_up: int = 0
    bytes_down: int = 0
    first_sizes: list[int] = field(default_factory=list)
    tls_ext_count: int = 0
    tls_alpn_class: AlpnClass = AlpnClass.NONE
    payload_seen: bool = False


def update_flow(state: FlowState, packet: PacketObservation) -> None:
    """Apply one packet to the slot state exactly as features.p4 does.

    Args:
        state: Slot state, mutated in place.
        packet: The packet being processed.
    """
    if state.pkt_count == 0:
        state.first_ts_us = packet.timestamp_us
    else:
        delta = timestamp_delta_us(packet.timestamp_us, state.last_ts_us)
        # §7.12: zero gaps are skipped; the first non-zero gap seeds the mean.
        if delta > 0 and state.iat_ewma == 0:
            state.iat_ewma = delta
        elif delta > 0:
            state.iat_ewma, state.iat_var_ewma = iat_update(
                state.iat_ewma, state.iat_var_ewma, delta
            )
    state.last_ts_us = packet.timestamp_us
    if len(state.first_sizes) < FIRST_SIZE_COUNT:
        state.first_sizes.append(packet.size_bytes)
    # §7.3: every counter saturates instead of wrapping.
    state.pkt_count = saturate(state.pkt_count + 1, 32)
    if packet.upstream:
        state.bytes_up = saturate(state.bytes_up + packet.size_bytes, 32)
    else:
        state.bytes_down = saturate(state.bytes_down + packet.size_bytes, 32)
    if packet.payload and not state.payload_seen:
        state.payload_seen = True
        state.tls_ext_count, state.tls_alpn_class = parse_client_hello_meta(packet.payload)


def feature_vector(state: FlowState, fanout_new_flows: int) -> tuple[int, ...]:
    """Return the frozen section 4.3 vector for the slot's current state, in FEATURE_ORDER."""
    return (
        state.iat_ewma,
        state.iat_var_ewma,
        state.pkt_count,
        mean_pkt_size_up(state.bytes_up, state.pkt_count),
        updown_ratio_x100(state.bytes_up, state.bytes_down),
        first8_size_bucket(state.first_sizes),
        saturate(fanout_new_flows, 16),
        state.tls_ext_count,
        int(state.tls_alpn_class),
        flow_age_ms(state.last_ts_us, state.first_ts_us),
    )


def checkpoint_vectors(
    packets: Sequence[PacketObservation], fanout_new_flows: int
) -> dict[int, tuple[int, ...]]:
    """Return the feature vector seen by the classifier at each reached checkpoint (6/16/64).

    Args:
        packets: The flow's packets in arrival order.
        fanout_new_flows: Sketch estimate for the flow's client at classification time.

    Returns:
        Checkpoint packet count mapped to the vector after that packet was processed.
    """
    state = FlowState()
    vectors: dict[int, tuple[int, ...]] = {}
    for index, packet in enumerate(packets, start=1):
        update_flow(state, packet)
        if index in FEATURE_CHECKPOINTS:
            vectors[index] = feature_vector(state, fanout_new_flows)
    return vectors
```

- [ ] **Step 4: Run tests and lint**

Run: `.venv/bin/pytest tests/test_extract_features.py tests/test_feature_math.py -v && make lint`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ml/extract_features.py tests/test_extract_features.py
git commit -m "P2.4: per-flow feature state mirror with checkpoint vectors"
```

### Task 4: Worked-example fixture + `docs/feature_arithmetic.md` (P2.4 DoD)

**Files:**
- Create: `tests/fixtures/feature_worked_examples.json` (generated once, committed)
- Create: `tests/test_feature_worked_examples.py`
- Create: `docs/feature_arithmetic.md`
- Modify: `docs/feature_arithmetic_design_gaps.md` (mark resolved → link), `docs/notebook.md`

**Interfaces:**
- Produces: the fixture file Dev A's PTF test consumes (same packet lists in, same registers and features out).

- [ ] **Step 1: Generate the fixture.** Write a throwaway script at `$SCRATCH/gen_worked_examples.py` (do not commit):

```python
import json
from dataclasses import asdict
from pathlib import Path

from common.contracts import FEATURE_CHECKPOINTS
from common.feature_math import flow_key_bytes, flow_slot, flow_tag
from ml.extract_features import FlowState, PacketObservation, feature_vector, update_flow
from tests.test_feature_math import _alpn, _client_hello


def packets(timestamps, sizes, upstream, payloads=None):
    payloads = payloads or [b""] * len(sizes)
    return [
        {"timestamp_us": t, "size_bytes": s, "upstream": u, "payload_hex": p.hex()}
        for t, s, u, p in zip(timestamps, sizes, upstream, payloads)
    ]


def cumulative(start, gaps):
    out, now = [], start
    for gap in gaps:
        now += gap
        out.append(now)
    return out


hello = _client_hello([(0x0000, b"\x00"), (0x0010, _alpn(b"h2")), (0x002B, b"\x02\x03\x04")])
human_gaps = [0, 100_000, 20_000, 350_000, 5_000, 150_000, 40_000, 900_000,
              12_000, 3_000, 600_000, 25_000, 8_000, 250_000, 70_000, 1_200_000]
human_sizes = [74, 1500, 66, 900, 66, 1500, 66, 1200, 74, 400, 66, 1500, 66, 800, 66, 1500]
scenarios = [
    ("agent_constant_pacing", packets([1_000_000 + 2_000 * i for i in range(64)],
     [400 if i % 2 == 0 else 1200 for i in range(64)], [i % 2 == 0 for i in range(64)])),
    ("human_variable_pacing", packets(cumulative(5_000_000, human_gaps), human_sizes,
     [i % 2 == 0 for i in range(16)])),
    ("tls_client_hello", packets(cumulative(0, [0, 500, 500, 500, 28_500, 500]),
     [74, 74, 66, 66 + len(hello), 1500, 66], [True, False, True, True, False, True],
     [b"", b"", b"", hello, b"", b""])),
]
flow = {"client_ip": 0x0A000001, "server_ip": 0x0A000064, "proto": 6,
        "client_port": 40000, "server_port": 443}
key = flow_key_bytes(**flow)
out = []
for name, pkts in scenarios:
    state, checkpoints = FlowState(), {}
    for index, p in enumerate(pkts, start=1):
        update_flow(state, PacketObservation(p["timestamp_us"], p["size_bytes"],
                                             p["upstream"], bytes.fromhex(p["payload_hex"])))
        if index in FEATURE_CHECKPOINTS:
            regs = asdict(state)
            regs["tls_alpn_class"] = int(regs["tls_alpn_class"])
            checkpoints[str(index)] = {"features": list(feature_vector(state, 1)),
                                       "registers": regs}
    out.append({"name": name, "flow": flow, "packets": pkts, "fanout_new_flows": 1,
                "expected": {"slot": flow_slot(key), "tag": flow_tag(key),
                             "checkpoints": checkpoints}})
Path("tests/fixtures").mkdir(exist_ok=True)
Path("tests/fixtures/feature_worked_examples.json").write_text(
    json.dumps({"spec": "docs/feature_arithmetic.md", "scenarios": out}, indent=1) + "\n")
```

Run: `PYTHONPATH=. .venv/bin/python $SCRATCH/gen_worked_examples.py`
Expected: `tests/fixtures/feature_worked_examples.json` exists. The `agent_constant_pacing` checkpoint "6" features are `[2000, 0, 6, 300, 58, 252, 1, 0, 0, 9]`.

- [ ] **Step 2: Write the regression test** — `tests/test_feature_worked_examples.py`:

```python
"""The reference arithmetic must keep reproducing the P2.4 worked examples Dev A's PTF uses."""

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from common.contracts import FEATURE_CHECKPOINTS
from common.feature_math import flow_key_bytes, flow_slot, flow_tag
from ml.extract_features import FlowState, PacketObservation, feature_vector, update_flow

FIXTURE = Path(__file__).parent / "fixtures" / "feature_worked_examples.json"
SCENARIOS = json.loads(FIXTURE.read_text(encoding="utf-8"))["scenarios"]


def _replay(scenario: dict) -> dict:
    state = FlowState()
    checkpoints = {}
    for index, packet in enumerate(scenario["packets"], start=1):
        update_flow(
            state,
            PacketObservation(
                packet["timestamp_us"],
                packet["size_bytes"],
                packet["upstream"],
                bytes.fromhex(packet["payload_hex"]),
            ),
        )
        if index in FEATURE_CHECKPOINTS:
            registers = json.loads(json.dumps(asdict(state)))
            features = list(feature_vector(state, scenario["fanout_new_flows"]))
            checkpoints[str(index)] = {"features": features, "registers": registers}
    key = flow_key_bytes(**scenario["flow"])
    return {"slot": flow_slot(key), "tag": flow_tag(key), "checkpoints": checkpoints}


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["name"])
def test_reference_reproduces_worked_example(scenario: dict) -> None:
    assert _replay(scenario) == scenario["expected"]


def test_examples_cover_every_checkpoint() -> None:
    reached = {cp for s in SCENARIOS for cp in s["expected"]["checkpoints"]}
    assert reached == {str(cp) for cp in FEATURE_CHECKPOINTS}


def test_fixture_contains_hand_checked_values() -> None:
    by_name = {s["name"]: s["expected"]["checkpoints"] for s in SCENARIOS}
    assert by_name["agent_constant_pacing"]["64"]["features"] == [2000, 0, 64, 200, 39, 255,
                                                                   1, 0, 0, 123]
    assert by_name["human_variable_pacing"]["6"]["features"] == [113086, 49707, 6, 51, 10, 84,
                                                                  1, 0, 0, 610]
    assert by_name["tls_client_hello"]["6"]["features"][7:9] == [3, 2]
```

Run `make fmt` so ruff normalizes the wrapped literals.

- [ ] **Step 3: Run**

Run: `.venv/bin/pytest tests/test_feature_worked_examples.py -v && make lint`
Expected: PASS (5 tests).

- [ ] **Step 4: Write `docs/feature_arithmetic.md`** with these sections:
  1. **Scope:** mirrors `common/feature_math.py` and `ml/extract_features.py`; approved in P2.4.
  2. **Per-packet order of operations:** claim/first_ts → Δ (48-bit wrap, 32-bit clamp; skip Δ==0; seed when `iat_ewma==0`) → `last_ts` → first_sizes (while <8) → `pkt_count` (sat 32) → `bytes_up`/`bytes_down` (sat 32, direction by ingress port) → first payload packet only: ClientHello parse → `reg_proto_meta`.
  3. **One subsection per feature (0–9):** formula, P4 shift/add form, saturation width, zero-input value. Copy each docstring from Task 2.
  4. **Hashing:** canonical key byte layout (104 bits), slot `crc32({key,8w0}) % 65536`, tag `crc32({key,8w1})`, sketch `crc32({src_ip,8w row}) % 4096`, counters 16-bit saturating. **Open check:** BMv2 crc32 == `zlib.crc32`, verified in P3.3.
  5. **ClientHello bounds:** complete extensions only, ≤16, both segment and block bounds; anything else → (0, NONE).
  6. **Worked examples:** a table of feature vectors at 6/16/64 per scenario, copied from the fixture, plus the hand derivation of `human_variable_pacing` packets 2–6 (EWMA steps 100000→90000→122500→107813→113086 and var 0→10000→41250→50781→49707).
  7. **Known approximations** (go into the paper): mean over-reads by <2×; ratio has power-of-two denominator steps; age is in 1.024 ms units; ClientHellos larger than one segment give partial counts; offline sketch epochs start at the capture's first packet (G17).

Replace the body of `docs/feature_arithmetic_design_gaps.md` with a one-paragraph "Resolved by P2.4 — see `docs/feature_arithmetic.md`" note that lists G1/G3/G4/G17 as open until approval. Add a dated `docs/notebook.md` entry with the decisions and Person 1's sign-off status.

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/feature_worked_examples.json tests/test_feature_worked_examples.py \
  docs/feature_arithmetic.md docs/feature_arithmetic_design_gaps.md docs/notebook.md
git commit -m "P2.4: worked examples at 6/16/64 packets and arithmetic spec"
```

- [ ] **Step 6: Approval gate (DoD).** Person 1 replays `tests/fixtures/feature_worked_examples.json` through `features.p4` in PTF. The registers and features must match `expected` exactly. Record the result in the notebook. Until this passes, P2.4 is **not done**; any mismatch gets fixed in the definition and both implementations.

### Task 5: P2.3 punt-path + reclassifier design doc

**Files:**
- Create: `docs/controller_design.md`

- [ ] **Step 1: Write the doc.** It has three mermaid sequence diagrams plus the decisions:
  1. **Startup (§6.1, §7.5):** `app` → `switch_api.read_register(policy_version)` → one batched CLI session: `table_clear` ×11 owned tables → `table_add` class actions, punt filter, tree entries → `meter_set_rates` ×4 → `register_write policy_version v+1` (last).
  2. **Punt (§6.2, §7.6):** BMv2 clone → CPU port veth → raw `AF_PACKET` reader → bounded queue (drop and count when full) → parse `cpu_header` (`CPU_HEADER_FIELDS`, 8 bytes) → Ethernet/IPv4/TCP → reason 1: `ja4()`, WBA verify if HTTP is visible → validate slot by `flow_tag(key) == reg_flow_key[slot]` → record `slot→(5-tuple, tag, evidence)` → evidence ⇒ override upsert (AGENT_INTERACTIVE) via a known handle.
  3. **Sweep every 2 s (§6.3, §6.4, §7.4, §7.8, §7.10):** one CLI session reads `[reg_pkt_count, reg_flow_key, reg_first_ts, reg_last_ts, reg_iat_ewma, reg_iat_var_ewma, reg_bytes_up, reg_bytes_down, reg_first_sizes, reg_proto_meta, reg_cm_sketch_{active}, reg_epoch_flag, reg_pkt_count]` → skip slots that advanced by more than 8 between the two `reg_pkt_count` reads (torn) → build features via `common/feature_math` → `CompiledTree.classify` → K=3 agreeing sweeps ⇒ upsert override → idle (count unchanged for 3 sweeps) or tag mismatch ⇒ delete override and forget the slot. Epoch tick every 1 s: write `reg_epoch_flag`; reset the retired sketch on the following tick.
  4. **Decisions:** G5–G12, G15 from this plan's gap table. Overrides are owned only by the controller and cleared on startup (TTL bookkeeping dies with the process). The punt CPU port name comes from `harness/topology.py` (agree with Dev C).
  5. **Budget check:** run a full-array read of 13 registers × 65536 in one CLI session in the VM and record its duration. If it exceeds 1.5 s, switch the sweep to the BMv2 Thrift Python client (`bm_runtime`). That switch is a design change for the team to raise at sync.

- [ ] **Step 2: Commit**

```bash
git add docs/controller_design.md
git commit -m "P2.3: punt path and reclassifier design"
```

### Task 6: `switch_api` — batched CLI sessions, error detection, bulk reads

**Files:**
- Modify: `controller/switch_api.py`
- Modify: `tests/test_switch_api.py`
- Create: `tests/fixtures/bmv2_cli/` (real transcripts, Step 6)

**Interfaces:**
- Produces: `SwitchApi.execute(commands: Sequence[str]) -> tuple[str, ...]` (one CLI session, one output per command, raises `SwitchApiError` on any rejected command), `SwitchApi.read_registers(names: Sequence[str]) -> dict[str, tuple[int, ...]]`, and `SwitchApi.read_register(name, index=None)` (unchanged signature).

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_switch_api.py`, and change the stdout of the existing `test_read_register_invokes_cli_with_validated_command` to `BANNER + "RuntimeCmd: policy_version[0]= 7\nRuntimeCmd: "`:

```python
BANNER = "Obtaining JSON from switch...\nDone\nControl utility for runtime P4 table manipulation\n"


def _cli(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def test_execute_runs_one_session_and_splits_outputs() -> None:
    stdout = BANNER + "RuntimeCmd: out one\nRuntimeCmd: out two\nRuntimeCmd: "
    with patch("controller.switch_api.subprocess.run", return_value=_cli(stdout)) as run:
        outputs = SwitchApi().execute(["cmd_a", "cmd_b"])
    assert outputs == ("out one\n", "out two\n")
    assert run.call_args.kwargs["input"] == "cmd_a\ncmd_b\n"
    run.assert_called_once()


@pytest.mark.parametrize(
    "rejection",
    [
        "Invalid table operation (DUPLICATE_ENTRY)",
        "Error: Invalid register name",
        "*** Unknown syntax: table_ad x",
    ],
)
def test_execute_raises_when_cli_rejects_a_command_despite_exit_zero(rejection: str) -> None:
    stdout = BANNER + "RuntimeCmd: ok\nRuntimeCmd: " + rejection + "\nRuntimeCmd: "
    with patch("controller.switch_api.subprocess.run", return_value=_cli(stdout)):
        with pytest.raises(SwitchApiError, match="cmd_b"):
            SwitchApi().execute(["cmd_a", "cmd_b"])


def test_execute_raises_on_short_transcript() -> None:
    with patch("controller.switch_api.subprocess.run", return_value=_cli(BANNER)):
        with pytest.raises(SwitchApiError, match="transcript"):
            SwitchApi().execute(["cmd_a"])


def test_execute_rejects_multiline_commands() -> None:
    with pytest.raises(ValueError, match="single line"):
        SwitchApi().execute(["register_reset reg_pkt_count\ntable_clear tbl_class_action"])


def test_read_registers_reads_many_arrays_in_one_session() -> None:
    stdout = (
        BANNER
        + "RuntimeCmd: reg_pkt_count= 0, 7\nRuntimeCmd: reg_last_ts= 5, 9\nRuntimeCmd: "
    )
    with patch("controller.switch_api.subprocess.run", return_value=_cli(stdout)) as run:
        values = SwitchApi().read_registers(["reg_pkt_count", "reg_last_ts"])
    assert values == {"reg_pkt_count": (0, 7), "reg_last_ts": (5, 9)}
    run.assert_called_once()


def test_read_registers_rejects_unknown_name_before_running_cli() -> None:
    with patch("controller.switch_api.subprocess.run") as run:
        with pytest.raises(ValueError, match="unknown contract register"):
            SwitchApi().read_registers(["reg_pkt_count", "invented"])
    run.assert_not_called()
```

Also rewrite the existing `test_read_register_surfaces_cli_failure` without the parenthesized `with (...)`. Tests run on 3.11, so this is only for consistency with the 3.9-safe style. Use nested `with` blocks as above.

- [ ] **Step 2: Run and confirm failure**

Run: `.venv/bin/pytest tests/test_switch_api.py -v`
Expected: FAIL with `AttributeError: 'SwitchApi' object has no attribute 'execute'`

- [ ] **Step 3: Implement.** Replace the body of `controller/switch_api.py` below the imports, keeping `_parse_register_output` unchanged:

```python
_PROMPT = "RuntimeCmd: "
# simple_switch_CLI exits 0 even when it rejects a command; these lines are its rejections.
_REJECTION = re.compile(r"^(?:Error|Invalid \w+ operation|\*\*\* Unknown syntax)", re.MULTILINE)


class SwitchApiError(RuntimeError):
    """Raised when BMv2 rejects a controller operation."""


class SwitchApi:
    """The only controller module that talks to BMv2, via `simple_switch_CLI` sessions."""

    def __init__(self, cli_path: Path = Path("simple_switch_CLI"), thrift_port: int = 9090) -> None:
        if thrift_port <= 0 or thrift_port > 65_535:
            raise ValueError("thrift_port must be between 1 and 65535")
        self._cli_path = cli_path
        self._thrift_port = thrift_port

    def execute(self, commands: Sequence[str]) -> tuple[str, ...]:
        """Run commands in one CLI session and return each command's output.

        Args:
            commands: Single-line CLI commands, executed in order.

        Returns:
            One output string per command.

        Raises:
            ValueError: If a command spans multiple lines.
            SwitchApiError: If the CLI fails or rejects any command.
        """
        if not commands:
            return ()
        for command in commands:
            if "\n" in command:
                raise ValueError(f"CLI commands must be a single line: {command!r}")
        try:
            result = subprocess.run(
                [str(self._cli_path), "--thrift-port", str(self._thrift_port)],
                input="".join(f"{command}\n" for command in commands),
                capture_output=True,
                check=False,
                text=True,
            )
        except OSError as exc:
            raise SwitchApiError(f"could not execute BMv2 CLI: {self._cli_path}") from exc
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic output"
            raise SwitchApiError(f"BMv2 CLI failed: {detail}")
        outputs = _split_transcript(result.stdout, len(commands))
        for command, output in zip(commands, outputs):
            if _REJECTION.search(output):
                raise SwitchApiError(f"BMv2 rejected '{command}': {output.strip()}")
        return outputs

    def read_registers(self, names: Sequence[str]) -> dict[str, tuple[int, ...]]:
        """Bulk-read whole register arrays in one CLI session (design.md section 6.3).

        Raises:
            ValueError: If a name is not a contract register.
            SwitchApiError: If the CLI fails or returns an unreadable response.
        """
        for name in names:
            _require_register(name)
        outputs = self.execute([f"register_read {name}" for name in names])
        return {name: _parse_register_output(name, out) for name, out in zip(names, outputs)}

    def read_register(self, register_name: str, index: int | None = None) -> tuple[int, ...]:
        """Read one register array, or one cell of it, by contract name.

        Raises:
            ValueError: If the register name or index is invalid.
            SwitchApiError: If the CLI fails or returns an unreadable response.
        """
        _require_register(register_name)
        if index is not None and index < 0:
            raise ValueError("register index must be non-negative")
        command = f"register_read {register_name}"
        if index is not None:
            command = f"{command} {index}"
        (output,) = self.execute([command])
        return _parse_register_output(register_name, output)


def _require_register(name: str) -> None:
    if name not in REGISTER_NAMES:
        raise ValueError(f"unknown contract register: {name}")


def _split_transcript(stdout: str, command_count: int) -> tuple[str, ...]:
    # The banner precedes the first prompt; each later prompt is followed by one output.
    outputs = stdout.split(_PROMPT)[1 : 1 + command_count]
    if len(outputs) != command_count:
        raise SwitchApiError(
            f"BMv2 CLI transcript had {len(outputs)} outputs for {command_count} commands"
        )
    return tuple(outputs)
```

Add `from collections.abc import Sequence` to the imports. In `pyproject.toml`, change the per-file ignores to `"controller/**" = ["UP", "B905"]` and add `"common/**" = ["B905"]`: Python 3.9 has no `zip(strict=)`, and ruff targets py311.

- [ ] **Step 4: Run tests and lint**

Run: `.venv/bin/pytest tests/test_switch_api.py -v && make lint`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add controller/switch_api.py tests/test_switch_api.py pyproject.toml
git commit -m "P3.2: batched BMv2 CLI sessions, rejection detection, bulk register reads"
```

- [ ] **Step 6: VM transcript check (Linux VM, `l2fwd.json` loaded).** Run:
  `printf 'register_read policy_version 0\nregister_read policy_version\ntable_add nope x 1 =>\n' | simple_switch_CLI --thrift-port 9090 > tests/fixtures/bmv2_cli/session.txt`
  Then add a test that parses this real file with `_split_transcript(..., 3)`, expects the first two outputs to parse, and expects the third to match `_REJECTION`. If the real prompt, rejection text or short-name lookup (`policy_version` vs `IngressPipeline.policy_version`) differs, fix the pattern or qualify names in `SwitchApi`. Commit the fixture and test.

### Task 7: `switch_api` — idempotent write command builders

**Files:**
- Modify: `controller/switch_api.py`
- Test: `tests/test_switch_api.py`

**Interfaces:**
- Produces: `MatchKey = Union[int, Tuple[int, int]]`, `table_add_command(table, action, keys, params, priority=None) -> str`, `table_clear_command(table) -> str`, `register_write_command(name, index, value) -> str`, `meter_set_rates_command(meter, index, rates: Sequence[tuple[float, int]]) -> str`. All validate names against contracts and values as unsigned.

- [ ] **Step 1: Write the failing tests** (append the functions; merge the imports below into the top-of-file import block):

```python
from common.contracts import CLASS_ACTION_TABLE, METER_AGENT_INTERACTIVE
from controller.switch_api import (
    meter_set_rates_command,
    register_write_command,
    table_add_command,
    table_clear_command,
)


def test_table_add_command_exact_and_range_keys_with_priority() -> None:
    command = table_add_command("tbl_tree_l0", "tree_next", [3, (0, 12), (5, 5)], [7], 1)
    assert command == "table_add tbl_tree_l0 tree_next 3 0->12 5->5 => 7 1"


def test_table_add_command_without_params() -> None:
    assert table_add_command("tbl_punt_filter", "punt", [1], []) == (
        "table_add tbl_punt_filter punt 1 =>"
    )


@pytest.mark.parametrize(
    ("table", "keys", "params"),
    [("tbl_invented", [1], []), (CLASS_ACTION_TABLE, [(5, 4)], []), (CLASS_ACTION_TABLE, [1], [-1])],
)
def test_table_add_command_rejects_invalid_input(table: str, keys: list, params: list) -> None:
    with pytest.raises(ValueError):
        table_add_command(table, "set_class_action", keys, params)


def test_clear_register_and_meter_commands() -> None:
    assert table_clear_command(CLASS_ACTION_TABLE) == "table_clear tbl_class_action"
    assert register_write_command("policy_version", 0, 4) == "register_write policy_version 0 4"
    assert meter_set_rates_command(METER_AGENT_INTERACTIVE, 1, [(0.5, 15000), (0.625, 15000)]) == (
        "meter_set_rates M_AI 1 0.500000:15000 0.625000:15000"
    )
    with pytest.raises(ValueError):
        register_write_command("invented", 0, 1)
    with pytest.raises(ValueError):
        meter_set_rates_command("M_X", 0, [(1.0, 1)])
```

- [ ] **Step 2: Run and confirm failure**

Run: `.venv/bin/pytest tests/test_switch_api.py -v`
Expected: FAIL with `ImportError: cannot import name 'meter_set_rates_command'`

- [ ] **Step 3: Implement** (append to `controller/switch_api.py`; extend imports with `from typing import Tuple, Union` and the contract names `METER_AGENT_BULK`, `METER_AGENT_INTERACTIVE`, `TABLE_NAMES`):

```python
MatchKey = Union[int, Tuple[int, int]]  # exact value, or inclusive (low, high) range


def table_add_command(
    table: str,
    action: str,
    keys: Sequence[MatchKey],
    params: Sequence[int],
    priority: int | None = None,
) -> str:
    """Build a `table_add`; range tables need `priority` (BMv2 orders overlapping ranges)."""
    _require_table(table)
    words = ["table_add", table, action, *(_format_key(key) for key in keys), "=>"]
    words += [str(_unsigned(param)) for param in params]
    if priority is not None:
        words.append(str(_unsigned(priority)))
    return " ".join(words)


def table_clear_command(table: str) -> str:
    """Build a `table_clear` for a contract table."""
    _require_table(table)
    return f"table_clear {table}"


def register_write_command(name: str, index: int, value: int) -> str:
    """Build a `register_write` for one cell of a contract register."""
    _require_register(name)
    return f"register_write {name} {_unsigned(index)} {_unsigned(value)}"


def meter_set_rates_command(meter: str, index: int, rates: Sequence[tuple[float, int]]) -> str:
    """Build `meter_set_rates`: (CIR, CBS) then (PIR, PBS), rates in bytes per microsecond."""
    if meter not in (METER_AGENT_INTERACTIVE, METER_AGENT_BULK):
        raise ValueError(f"unknown contract meter: {meter}")
    bands = " ".join(f"{rate:.6f}:{_unsigned(burst)}" for rate, burst in rates)
    return f"meter_set_rates {meter} {_unsigned(index)} {bands}"


def _require_table(table: str) -> None:
    if table not in TABLE_NAMES:
        raise ValueError(f"unknown contract table: {table}")


def _unsigned(value: int) -> int:
    if value < 0:
        raise ValueError(f"BMv2 values are unsigned, got {value}")
    return value


def _format_key(key: MatchKey) -> str:
    if isinstance(key, int):
        return str(_unsigned(key))
    low, high = key
    if not 0 <= low <= high:
        raise ValueError(f"invalid range key {key}")
    return f"{low}->{high}"
```

- [ ] **Step 4: Run tests and lint**

Run: `.venv/bin/pytest tests/test_switch_api.py -v && make lint`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add controller/switch_api.py tests/test_switch_api.py
git commit -m "P3.2: validated BMv2 write command builders"
```

### Task 8: `common/compiled_tree.py` — §4.5 loader and validator

**Files:**
- Create: `common/compiled_tree.py`
- Test: `tests/test_compiled_tree.py`

**Interfaces:**
- Produces: `TreeEntry(table, match_ranges, action, action_params, priority)`, `CompiledTree(model_hash, feature_order, entries)`, `CompiledTreeError(ValueError)`, `load_compiled_tree(path) -> CompiledTree`, `parse_compiled_tree(raw) -> CompiledTree`. `match_ranges[0]` is `tree_node` (low == high); entries 1–10 follow `FEATURE_ORDER`. P3.7 adds `CompiledTree.classify` here.

- [ ] **Step 1: Write the failing tests** — `tests/test_compiled_tree.py`:

```python
"""Schema checks for the section 4.5 compiled-tree file (trust boundary for controller and ml)."""

import json
from pathlib import Path

import pytest

from common.compiled_tree import CompiledTreeError, load_compiled_tree, parse_compiled_tree
from common.contracts import FEATURE_BIT_WIDTHS, FEATURE_ORDER


def _entry(**overrides: object) -> dict:
    entry = {
        "table": "tbl_tree_l0",
        "match_ranges": [[0, 0]] + [[0, (1 << bits) - 1] for bits in FEATURE_BIT_WIDTHS],
        "action": "tree_leaf",
        "action_params": [1, 0],
        "priority": 1,
    }
    entry.update(overrides)
    return entry


def _tree(*entries: dict) -> dict:
    return {"model_hash": "abc123", "feature_order": list(FEATURE_ORDER), "entries": list(entries)}


def test_parses_valid_tree(tmp_path: Path) -> None:
    path = tmp_path / "compiled_tree.json"
    path.write_text(json.dumps(_tree(_entry())), encoding="utf-8")
    tree = load_compiled_tree(path)
    assert tree.model_hash == "abc123"
    assert tree.entries[0].match_ranges[0] == (0, 0)
    assert tree.entries[0].action_params == (1, 0)


@pytest.mark.parametrize(
    "raw",
    [
        {**_tree(), "feature_order": list(reversed(FEATURE_ORDER))},  # §7.9 skew
        {**_tree(), "extra": 1},
        _tree(_entry(table="tbl_class_action")),
        _tree(_entry(action="drop")),
        _tree(_entry(match_ranges=[[0, 1]] + [[0, 1]] * 10)),  # node must be exact
        _tree(_entry(match_ranges=[[0, 0]] + [[0, 1 << 32]] * 10)),  # wider than field
        _tree(_entry(match_ranges=[[0, 0]] * 3)),
        _tree(_entry(action_params=[True, 0])),
        _tree(_entry(priority=-1)),
    ],
)
def test_rejects_schema_violations(raw: dict) -> None:
    with pytest.raises(CompiledTreeError):
        parse_compiled_tree(raw)


def test_unreadable_file_raises(tmp_path: Path) -> None:
    with pytest.raises(CompiledTreeError, match="cannot read"):
        load_compiled_tree(tmp_path / "missing.json")
```

- [ ] **Step 2: Run and confirm failure**

Run: `.venv/bin/pytest tests/test_compiled_tree.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'common.compiled_tree'`

- [ ] **Step 3: Implement** — `common/compiled_tree.py`:

```python
"""Loader for the section 4.5 compiled-tree JSON, shared by ml/ and controller/ (3.9 safe)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from common.contracts import (
    COMPILED_TREE_ENTRY_FIELDS,
    COMPILED_TREE_FIELDS,
    FEATURE_BIT_WIDTHS,
    FEATURE_ORDER,
    TREE_LEAF_ACTION,
    TREE_NEXT_ACTION,
    TREE_NODE_BITS,
    TREE_TABLE_NAMES,
)

_KEY_BIT_WIDTHS = (TREE_NODE_BITS, *FEATURE_BIT_WIDTHS)


class CompiledTreeError(ValueError):
    """Raised when a compiled-tree file violates the section 4.5 schema."""


@dataclass(frozen=True)
class TreeEntry:
    """One range-match entry of a tbl_tree_l* table; match_ranges follow TREE_KEY_FIELDS."""

    table: str
    match_ranges: tuple[tuple[int, int], ...]
    action: str
    action_params: tuple[int, ...]
    priority: int


@dataclass(frozen=True)
class CompiledTree:
    """A validated compiled tree ready to push or evaluate."""

    model_hash: str
    feature_order: tuple[str, ...]
    entries: tuple[TreeEntry, ...]


def load_compiled_tree(path: Path) -> CompiledTree:
    """Read and validate a compiled-tree file.

    Raises:
        CompiledTreeError: If the file is unreadable or violates the schema.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CompiledTreeError(f"cannot read compiled tree {path}: {exc}") from exc
    return parse_compiled_tree(raw)


def parse_compiled_tree(raw: object) -> CompiledTree:
    """Validate decoded compiled-tree JSON against the section 4.5 schema.

    Raises:
        CompiledTreeError: On any schema violation, including feature-order skew (7.9).
    """
    fields = _require_fields(raw, COMPILED_TREE_FIELDS, "compiled tree")
    if fields["feature_order"] != list(FEATURE_ORDER):
        raise CompiledTreeError("feature_order differs from contracts.FEATURE_ORDER (7.9)")
    if not isinstance(fields["model_hash"], str) or not fields["model_hash"]:
        raise CompiledTreeError("model_hash must be a non-empty string")
    if not isinstance(fields["entries"], list):
        raise CompiledTreeError("entries must be a list")
    entries = tuple(_parse_entry(entry) for entry in fields["entries"])
    return CompiledTree(fields["model_hash"], FEATURE_ORDER, entries)


def _parse_entry(raw: object) -> TreeEntry:
    fields = _require_fields(raw, COMPILED_TREE_ENTRY_FIELDS, "tree entry")
    if fields["table"] not in TREE_TABLE_NAMES:
        raise CompiledTreeError(f"not a tree table: {fields['table']!r}")
    if fields["action"] not in (TREE_NEXT_ACTION, TREE_LEAF_ACTION):
        raise CompiledTreeError(f"not a tree action: {fields['action']!r}")
    ranges = fields["match_ranges"]
    if not isinstance(ranges, list) or len(ranges) != len(_KEY_BIT_WIDTHS):
        raise CompiledTreeError(f"match_ranges needs {len(_KEY_BIT_WIDTHS)} [low, high] pairs")
    match_ranges = tuple(_parse_range(pair, bits) for pair, bits in zip(ranges, _KEY_BIT_WIDTHS))
    if match_ranges[0][0] != match_ranges[0][1]:
        raise CompiledTreeError("tree_node is an exact match: low must equal high")
    params = fields["action_params"]
    if not isinstance(params, list) or not all(_is_uint(param) for param in params):
        raise CompiledTreeError("action_params must be unsigned integers")
    if not _is_uint(fields["priority"]):
        raise CompiledTreeError("priority must be an unsigned integer")
    return TreeEntry(
        fields["table"], match_ranges, fields["action"], tuple(params), fields["priority"]
    )


def _require_fields(raw: object, expected: Sequence[str], what: str) -> dict:
    if not isinstance(raw, dict) or set(raw) != set(expected):
        raise CompiledTreeError(f"{what} must have exactly the fields {tuple(expected)}")
    return raw


def _parse_range(raw: object, bits: int) -> tuple[int, int]:
    if (
        not isinstance(raw, list)
        or len(raw) != 2
        or not all(_is_uint(value) for value in raw)
        or not raw[0] <= raw[1] < (1 << bits)
    ):
        raise CompiledTreeError(f"bad match range {raw!r} for a {bits}-bit field")
    return raw[0], raw[1]


def _is_uint(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0
```

- [ ] **Step 4: Run tests and lint**

Run: `.venv/bin/pytest tests/test_compiled_tree.py -v && make lint`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add common/compiled_tree.py tests/test_compiled_tree.py
git commit -m "P3.2: compiled-tree schema loader shared by controller and ml"
```

### Task 9: `controller/policy.py` — full idempotent startup push with policy_version

**Files:**
- Create: `controller/policy.py`
- Create: `tests/fake_bmv2.py`
- Test: `tests/test_policy.py`

**Interfaces:**
- Consumes: `SwitchApi.execute`, `SwitchApi.read_register`, Task 7 builders, `CompiledTree`.
- Produces: `push_policy(api, link_mbps, tree) -> int` (new version), `policy_commands(link_mbps, tree) -> list[str]`, `link_share_bytes_per_us(link_mbps, pct) -> float`.

- [ ] **Step 1: Write the fake switch** — `tests/fake_bmv2.py`:

```python
"""In-memory stand-in for simple_switch_CLI with BMv2's duplicate-entry rule."""

from __future__ import annotations

from collections.abc import Sequence

from common.contracts import TREE_TABLE_NAMES
from controller.switch_api import SwitchApiError


class FakeBmv2:
    """Executes the CLI commands the controller emits; can crash after N commands."""

    def __init__(self, crash_after: int | None = None) -> None:
        self.tables: dict[str, dict[str, str]] = {}
        self.registers: dict[tuple[str, int], int] = {}
        self.meters: dict[tuple[str, int], str] = {}
        self.crash_after = crash_after
        self.executed = 0

    def read_register(self, name: str, index: int | None = None) -> tuple[int, ...]:
        return (self.registers.get((name, index or 0), 0),)

    def execute(self, commands: Sequence[str]) -> tuple[str, ...]:
        return tuple(self._run(command) for command in commands)

    def _run(self, command: str) -> str:
        if self.crash_after is not None and self.executed >= self.crash_after:
            raise SwitchApiError("simulated controller crash")
        self.executed += 1
        verb, *args = command.split()
        if verb == "table_clear":
            self.tables[args[0]] = {}
        elif verb == "table_add":
            table, separator = args[0], args.index("=>")
            key = " ".join(args[2:separator])
            if table in TREE_TABLE_NAMES:
                key += f" prio={args[-1]}"  # range tables key on match + priority
            entries = self.tables.setdefault(table, {})
            if key in entries:
                raise SwitchApiError(f"Invalid table operation (DUPLICATE_ENTRY): {command}")
            entries[key] = command
        elif verb == "register_write":
            self.registers[(args[0], int(args[1]))] = int(args[2])
        elif verb == "meter_set_rates":
            self.meters[(args[0], int(args[1]))] = " ".join(args[2:])
        else:
            raise SwitchApiError(f"*** Unknown syntax: {command}")
        return ""
```

- [ ] **Step 2: Write the failing tests** — `tests/test_policy.py`:

```python
"""Startup policy push: contents, idempotence, crash/restart safety (design.md 6.1, 7.5)."""

import pytest

from common.compiled_tree import parse_compiled_tree
from common.contracts import (
    CLASS_ACTION_TABLE,
    FEATURE_BIT_WIDTHS,
    FEATURE_ORDER,
    FLOW_OVERRIDE_TABLE,
    METER_AGENT_BULK,
    METER_AGENT_INTERACTIVE,
    POLICY_VERSION_REGISTER,
    PRESET_PROTECTIVE,
)
from controller.policy import link_share_bytes_per_us, policy_commands, push_policy
from controller.switch_api import SwitchApiError
from tests.fake_bmv2 import FakeBmv2

TREE = parse_compiled_tree(
    {
        "model_hash": "h1",
        "feature_order": list(FEATURE_ORDER),
        "entries": [
            {
                "table": "tbl_tree_l0",
                "match_ranges": [[0, 0]] + [[0, (1 << b) - 1] for b in FEATURE_BIT_WIDTHS],
                "action": "tree_leaf",
                "action_params": [1, 0],
                "priority": 1,
            }
        ],
    }
)


def test_push_installs_class_actions_meters_tree_and_bumps_version() -> None:
    switch = FakeBmv2()
    assert push_policy(switch, link_mbps=20, tree=TREE) == 1
    assert switch.tables[CLASS_ACTION_TABLE]["1"].endswith("=> 34 2 0 48")  # §4.1 HUMAN
    assert switch.tables[CLASS_ACTION_TABLE]["0"].endswith("=> 0 1 0 0")  # UNKNOWN: no ECN
    assert len(switch.tables["tbl_tree_l0"]) == 1
    assert switch.tables[FLOW_OVERRIDE_TABLE] == {}
    assert switch.meters[(METER_AGENT_INTERACTIVE, PRESET_PROTECTIVE)] == (
        "0.500000:15000 0.625000:15000"
    )
    assert switch.registers[(POLICY_VERSION_REGISTER, 0)] == 1


def test_restart_repush_is_idempotent_and_version_is_monotonic() -> None:
    switch = FakeBmv2()
    push_policy(switch, 20, TREE)
    first = {name: dict(entries) for name, entries in switch.tables.items()}
    assert push_policy(switch, 20, TREE) == 2  # §7.5: no DUPLICATE_ENTRY on re-push
    assert switch.tables == first


def test_crash_mid_push_leaves_old_version_and_next_start_recovers() -> None:
    switch = FakeBmv2()
    push_policy(switch, 20, TREE)
    switch.crash_after = switch.executed + 15
    with pytest.raises(SwitchApiError, match="crash"):
        push_policy(switch, 20, TREE)
    assert switch.registers[(POLICY_VERSION_REGISTER, 0)] == 1  # version written last
    switch.crash_after = None
    assert push_policy(switch, 20, TREE) == 2


def test_push_without_tree_leaves_tree_tables_empty_fail_open() -> None:
    switch = FakeBmv2()
    push_policy(switch, 20, None)
    assert switch.tables["tbl_tree_l0"] == {}


def test_link_share_and_link_bounds() -> None:
    assert link_share_bytes_per_us(20, 20) == 0.5
    assert link_share_bytes_per_us(50, 100) == 6.25
    with pytest.raises(ValueError):
        policy_commands(5, None)


def test_bulk_meter_presets_both_installed() -> None:
    switch = FakeBmv2()
    push_policy(switch, 20, None)
    assert {key[0] for key in switch.meters} == {METER_AGENT_INTERACTIVE, METER_AGENT_BULK}
    assert {key[1] for key in switch.meters} == {0, 1}
```

- [ ] **Step 3: Run and confirm failure**

Run: `.venv/bin/pytest tests/test_policy.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'controller.policy'`

- [ ] **Step 4: Implement** — `controller/policy.py`:

```python
"""Startup policy push: class actions, meter presets, tree entries (design.md 6.1, 6.5)."""

from __future__ import annotations

import logging

from common.compiled_tree import CompiledTree
from common.contracts import (
    CLASS_ACTION_NAME,
    CLASS_ACTION_TABLE,
    CLASS_TREATMENTS,
    FLOW_OVERRIDE_TABLE,
    MAX_LINK_MBPS,
    METER_BURST_BYTES,
    METER_PRESETS,
    METER_SELECT,
    MIN_LINK_MBPS,
    POLICY_VERSION_REGISTER,
    PUNT_ACTION,
    PUNT_FILTER_TABLE,
    PUNT_REASON_FIRST_PACKET,
    PUNT_REASON_LOW_CONFIDENCE,
    TREE_TABLE_NAMES,
)
from controller.switch_api import (
    SwitchApi,
    meter_set_rates_command,
    register_write_command,
    table_add_command,
    table_clear_command,
)

LOGGER = logging.getLogger(__name__)

# §7.5: every controller-owned table is rebuilt on each push. Overrides are cleared too: their
# TTL bookkeeping died with the previous controller process, and the tree stays authoritative.
_OWNED_TABLES = (*TREE_TABLE_NAMES, CLASS_ACTION_TABLE, PUNT_FILTER_TABLE, FLOW_OVERRIDE_TABLE)
_PUNT_REASONS = (PUNT_REASON_FIRST_PACKET, PUNT_REASON_LOW_CONFIDENCE)


def push_policy(api: SwitchApi, link_mbps: int, tree: CompiledTree | None) -> int:
    """Push the full policy, then advance policy_version.

    Safe at any time (section 7.5): owned tables are cleared before being filled, meters and
    registers are overwritten, and the version is written last, so a push cut short by a crash
    leaves the old version and the next start re-pushes over it.

    Args:
        api: Switch boundary.
        link_mbps: Bottleneck rate from the experiment config; scales the meter presets.
        tree: Compiled classifier, or None before one exists (every flow stays UNKNOWN).

    Returns:
        The policy version now stored on the switch.

    Raises:
        SwitchApiError: If BMv2 rejects any command.
        ValueError: If link_mbps is outside the contract range.
    """
    (current,) = api.read_register(POLICY_VERSION_REGISTER, 0)
    if tree is None:
        LOGGER.warning("no compiled tree; all flows classify as UNKNOWN (fail-open)")
    api.execute(policy_commands(link_mbps, tree))
    version = current + 1
    api.execute([register_write_command(POLICY_VERSION_REGISTER, 0, version)])
    LOGGER.info("pushed policy version %d (model %s)", version, tree.model_hash if tree else "-")
    return version


def policy_commands(link_mbps: int, tree: CompiledTree | None) -> list[str]:
    """Return every CLI command of a full policy push, in execution order."""
    commands = [table_clear_command(table) for table in _OWNED_TABLES]
    commands += _class_action_commands()
    commands += [table_add_command(PUNT_FILTER_TABLE, PUNT_ACTION, [r], []) for r in _PUNT_REASONS]
    commands += _meter_preset_commands(link_mbps)
    if tree is not None:
        commands += [
            table_add_command(
                entry.table,
                entry.action,
                [entry.match_ranges[0][0], *entry.match_ranges[1:]],
                entry.action_params,
                entry.priority,
            )
            for entry in tree.entries
        ]
    return commands


def link_share_bytes_per_us(link_mbps: int, pct: int) -> float:
    """Convert a link share to BMv2 byte-meter units: Mbps / 8 bytes per microsecond."""
    return link_mbps * pct / 800


def _class_action_commands() -> list[str]:
    return [
        table_add_command(
            CLASS_ACTION_TABLE,
            CLASS_ACTION_NAME,
            [int(traffic_class)],
            [
                treatment.dscp,
                treatment.queue_priority,
                METER_SELECT[treatment.meter_name],
                treatment.ecn_threshold_pkts or 0,  # 0 disables ECN marking
            ],
        )
        for traffic_class, treatment in CLASS_TREATMENTS.items()
    ]


def _meter_preset_commands(link_mbps: int) -> list[str]:
    # §5.5: both presets are installed; the data plane picks one by reg_congestion_flag.
    if not MIN_LINK_MBPS <= link_mbps <= MAX_LINK_MBPS:
        raise ValueError(f"link_mbps must be within {MIN_LINK_MBPS}..{MAX_LINK_MBPS}")
    return [
        meter_set_rates_command(
            meter,
            preset,
            [
                (link_share_bytes_per_us(link_mbps, share.cir_pct), METER_BURST_BYTES),
                (link_share_bytes_per_us(link_mbps, share.pir_pct), METER_BURST_BYTES),
            ],
        )
        for preset, shares in METER_PRESETS.items()
        for meter, share in shares.items()
    ]
```

- [ ] **Step 5: Run tests and lint**

Run: `.venv/bin/pytest tests/test_policy.py -v && make lint`
Expected: PASS (6 tests).

- [ ] **Step 6: Commit**

```bash
git add controller/policy.py tests/fake_bmv2.py tests/test_policy.py
git commit -m "P3.2: idempotent startup policy push with policy_version (7.5)"
```

### Task 10: Wire startup into `controller/app.py`, Python 3.9 gate, live restart test

**Files:**
- Modify: `controller/app.py`
- Create: `tests/test_app.py`
- Modify: `Makefile`, `.github/workflows/ci.yml`, `docs/notebook.md`, `docs/review_1_status.md` (evidence line only)

**Interfaces:**
- Produces: `python -m controller.app --link-mbps N [--compiled-tree PATH] [--cli-path] [--thrift-port]`, which returns 0 after the push and 1 on any startup error.

- [ ] **Step 1: Write the failing tests** — `tests/test_app.py`:

```python
"""Controller entrypoint: startup push and fail-fast exit codes."""

from pathlib import Path
from unittest.mock import patch

from controller import app
from controller.switch_api import SwitchApiError


def test_main_pushes_policy_and_returns_zero() -> None:
    with patch("controller.app.push_policy", return_value=3) as push:
        assert app.main(["--link-mbps", "20"]) == 0
    assert push.call_args.args[1:] == (20, None)


def test_main_returns_one_on_switch_error() -> None:
    with patch("controller.app.push_policy", side_effect=SwitchApiError("down")):
        assert app.main(["--link-mbps", "20"]) == 1


def test_main_returns_one_on_bad_tree_file(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    assert app.main(["--link-mbps", "20", "--compiled-tree", str(missing)]) == 1
```

- [ ] **Step 2: Run and confirm failure**

Run: `.venv/bin/pytest tests/test_app.py -v`
Expected: FAIL (`error: unrecognized arguments: --link-mbps`, SystemExit 2).

- [ ] **Step 3: Implement** — replace `controller/app.py`:

```python
"""Controller entrypoint: startup policy push (design.md section 6.1, task P3.2)."""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from pathlib import Path

from common.compiled_tree import load_compiled_tree
from controller.policy import push_policy
from controller.switch_api import SwitchApi, SwitchApiError

LOGGER = logging.getLogger(__name__)


def main(argv: Sequence[str] | None = None) -> int:
    """Push the full policy to BMv2 and report the resulting policy version.

    Args:
        argv: Optional command-line arguments for tests or programmatic use.

    Returns:
        Zero when the policy is installed, otherwise one.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli-path", type=Path, default=Path("simple_switch_CLI"))
    parser.add_argument("--thrift-port", type=int, default=9090)
    parser.add_argument("--link-mbps", type=int, required=True)
    parser.add_argument("--compiled-tree", type=Path, default=None)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        tree = load_compiled_tree(args.compiled_tree) if args.compiled_tree else None
        version = push_policy(SwitchApi(args.cli_path, args.thrift_port), args.link_mbps, tree)
    except (SwitchApiError, ValueError) as exc:
        LOGGER.error("controller startup failed: %s", exc)
        return 1
    LOGGER.info("controller ready; policy version=%d", version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Add the Python 3.9 gate.** In `Makefile`, add `PYTHON39 ?= python3.9`, add `check-py39` to `.PHONY`, and add:

```make
# controller/ runs in the Ryu 3.9 venv (docs/ENVIRONMENT.md section 4)
check-py39:
	$(PYTHON39) -m compileall -q common controller
	$(PYTHON39) -c "import common.feature_math, controller.app"
```

In `.github/workflows/ci.yml`, add the job:

```yaml
  controller-py39:
    runs-on: ubuntu-22.04
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.9"
      - run: make check-py39 PYTHON39=python
```

- [ ] **Step 5: Run everything**

Run: `make lint && make test`, then `make check-py39` if a local 3.9 exists (otherwise rely on CI).
Expected: all green. Test count = 34 existing + the new ones.

- [ ] **Step 6: Commit**

```bash
git add controller/app.py tests/test_app.py Makefile .github/workflows/ci.yml
git commit -m "P3.2: controller startup push entrypoint and Python 3.9 import gate"
```

- [ ] **Step 7: Live kill/restart test (Linux VM; needs Person 1's `agent_aware.p4` with `tbl_class_action`, `tbl_tree_l*`, `tbl_punt_filter`, `tbl_flow_override`, `M_AI`/`M_AB` [size 2], `policy_version`).** Procedure (DoD for P3.2):
  1. Start `choke_v1` with the program and run `iperf3 -t 60` from `h_bulk`.
  2. `python -m controller.app --link-mbps 20 &` then `sleep 0.3; kill -9 $!` to kill it mid-push.
  3. `python -m controller.app --link-mbps 20` should exit 0.
  4. Check: `table_num_entries tbl_class_action` = 4. `register_read policy_version 0` went up. `meter_get_rates M_AI 1` matches the protective preset. The iperf3 report shows continuous throughput with no stall longer than one interval.
  5. Run step 3 again: exit 0, version +1, identical entry counts.

  Record the transcript in `docs/notebook.md`. Until this has run, report P3.2 as "unit-verified, live restart pending", not done.

---

## PHASE B–F — roadmap (one detailed writing-plans pass per item when its blockers clear)

Each item below already has its file layout, interfaces and tests decided, so its detailed plan is mostly mechanical.

### Item 3 — P3.3 bit-exact pcap extraction
- **Blocked by:** Task 4 approval; Person 1's P3.1 `features.p4`. Person 1 also supplies a shared pcap plus a PTF dump with the per-packet switch timestamp (`reg_last_ts` read after each packet) and the final value of every feature register and `reg_cm_sketch_*` (G14).
- **Files:** `ml/extract_features.py` (extend), `tests/test_extract_features.py`, `tests/fixtures/switch_dumps/`.
- **Interfaces:**
  - `extract_flows(pcap: Path, upstream_by_initiator=True) -> dict[FlowKey, list[PacketObservation]]` (scapy `PcapReader`, IPv4 TCP/UDP only, non-first fragments and non-IPv4 skipped per §7.15).
  - `CmSketch` with `claim(src_ip, now_us)`, `estimate(src_ip)`, epochs starting at the first packet (G17).
  - `extract_features(pcap, labels_csv) -> pandas.DataFrame` with columns `flow_id, checkpoint, *FEATURE_ORDER, label, source_framework`. Labels are joined from `harness/capture.py` `labels.csv` (never derived from features, §7.20).
  - `replay_with_switch_timestamps(packets, timestamps_us)` for the bit-exact check.
- **Mandatory tests:** a malformed or truncated pcap raises `PcapInputError` (trust boundary). Flows are bidirectional under one canonical key. Three flows from one source give fan-out 3. A sketch epoch rollover resets counts. Slot/tag/sketch indices **equal Dev A's dump** (proves G4 crc32 equality).
- **DoD check:** a pytest compares the Python registers and features for every slot in the shared-pcap dump against Dev A's dump; it must be 100% equal.

### Item 4 — P3.6 train + compile, P3.7 equivalence gate
- **Blocked by:** item 3; Dev C corpus with ≥4 frameworks (P3.8).
- **Files:** `ml/train.py`, `ml/compile_tree.py`, `ml/verify_equivalence.py`, `common/compiled_tree.py` (add `classify`), the generated section in `p4src/classifier.p4` (between `// BEGIN GENERATED: ml/compile_tree.py` and `// END GENERATED`, agreed with Person 1), `.github/workflows/ci.yml`.
- **Interfaces:**
  - `train(features: DataFrame, seed: int) -> TrainResult`: `DecisionTreeClassifier(max_depth=8, class_weight="balanced", random_state=seed)`. 5-fold `StratifiedGroupKFold` grouped by `flow_id`, so a flow's checkpoint rows never sit on both sides of a split. A 20% grouped held-out split.
  - Writes `model.pkl`, `report.json` (feature importances, CV scores, held-out per-class P/R per checkpoint) and `held_out.csv`.
  - Path-coverage assertion: every root→leaf path uses ≥1 of `TIMING_BURST_FEATURES` (§7.14). Training fails loudly otherwise.
  - `compile_tree(model) -> dict` (§4.5). Each internal node at depth d becomes 2 entries in `tbl_tree_l{d}`: left `[0, floor(thr)]`, right `[floor(thr)+1, max]`, other features wildcarded. A child that is a leaf compiles to `tree_leaf(class, 0)`, or `tree_leaf(UNKNOWN, 2)` when purity < 0.85 (G15). A single-leaf tree becomes one level-0 wildcard entry. `model_hash` = sha256 of the canonical JSON of the tree arrays (deterministic, unlike a pickle hash).
  - `emit_p4_declarations() -> str`: 8 tables, each keyed on `tree_node` exact plus 10 range fields, with size `2^(d+1)`. It depends only on contracts, so P4 and the entries can't drift.
  - `CompiledTree.classify(features) -> tuple[TrafficClass, int, int]` returns (class, punt_reason, leaf node), following BMv2 range/priority semantics.
  - `verify(model, compiled, held_out) -> EquivalenceReport`: compares leaf identity and the label after the purity rule. Exit 1 unless 100% match.
- **P4 rule:** emit declarations one table at a time. A human runs `p4c-bm2-ss` and pastes the errors back before the next table.
- **CI:** the gate runs on a deterministic synthetic dataset (property test). The real-corpus report is committed from `make equivalence` (corpus isn't available in CI).
- **Mandatory tests:** integer thresholds at the boundaries (thr=12.5 sends 12 left and 13 right). A path-coverage violation raises. A low-purity leaf goes to UNKNOWN with punt 2. The compiled JSON round-trips through `parse_compiled_tree`. `classify` agrees with sklearn on 10k random vectors including values at field max.

### Item 5 — P3.11 punt handler + reclassifier
- **Blocked by:** Person 1's P3.9 punt path; item 4; G8–G12 decisions; `cryptography` sign-off (G12).
- **Files:** `controller/ja4.py`, `controller/punt_handler.py`, `controller/reclassifier.py`, `controller/switch_api.py` (add `table_modify_command`, `table_delete_command`, `register_reset_command`, `parse_entry_handle`), `controller/app.py` (run loop), and tests for each.
- **Interfaces:**
  - `ja4(client_hello: bytes, transport="t") -> str` follows the FoxIO spec. Vectors come from FoxIO's published pcaps, cross-checked with tshark ≥4.2 `tls.handshake.ja4`.
  - `parse_cpu_header(frame: bytes) -> CpuHeader`.
  - `PuntHandler.handle(frame)`: bounded queue, drop-and-count when full (§7.6).
  - `OverrideTable` tracks 5-tuple → (handle, class, source, slot, tag); its methods are upsert/delete; it is cleared at startup.
  - `Reclassifier.sweep(snapshot: RegisterSnapshot) -> list[OverrideChange]` is a pure function and gets unit-tested without a switch.
  - `EpochTicker.tick()` flips `reg_epoch_flag`, then resets the retired sketch on the next tick (§7.10).
- **Mandatory tests:**
  - JA4 vectors.
  - WBA: valid, invalid and absent signature.
  - cpu_header round trip.
  - Punt flood: queue stays bounded and drops are counted.
  - Hysteresis: 2 sweeps install nothing; the 3rd installs; alternating labels never install (§7.8).
  - Torn read: a slot that advanced by more than 8 is skipped (§7.4).
  - Aging: 3 unchanged sweeps remove the override (§6.4).
  - Tag mismatch: the slot is forgotten (§7.11).
  - Evidence overrides are not changed by the reclassifier (G11).
  - Controller restart clears overrides (§7.5).
- **DoD check:** a VM chaos script kills/restarts the controller and floods punts. A flow whose pacing changes mid-run gets an override, observed by DSCP in the egress capture.

### Item 6 — P3.14 normal/protective presets
- **Blocked by:** Person 1's P3.13 `qos.p4` congestion flag; Dev C's burst presets.
- **Work:** the presets already ship in Task 1/Task 9. Tune `METER_PRESETS` with Dev C using config-driven runs (changes are a contract PR, with every run logged in the notebook). Add `meter_get_rates` readback to `switch_api`, plus a test that the installed rates equal the computed values.
- **DoD check:** a VM run with iperf overload from `h_bulk` plus agent traffic: `reg_congestion_flag` = 1 within HI, clears at LO, agent egress rate ≈ protective CIR, no human drops. Record the trace.

### Item 7 — P4.6 per-checkpoint P/R, P4.5 evasion
- **P4.6 (blocked by items 4–5, Dev C P3.12 `eval/metrics.py`):** add `per_checkpoint_pr(egress_pcap, labels_csv, checkpoints) -> DataFrame` to `eval/metrics.py` via a PR that Dev C reviews. Each egress packet's class comes from its DSCP through `CLASS_TREATMENTS` (inverse map). Classes are taken at packet index 6/16/64 per flow and compared with labels. This is Fig. 3 data, seeded and config-driven.
- **P4.5 (STOP: G13 contract change first):** after `think_time_profile` is approved in §4.6, Dev C's runners sample think time from the human IAT distribution (seeded). Measure: (a) offline accuracy via `extract_features` + `CompiledTree.classify`; (b) live accuracy with the reclassifier on; (c) slowdown in agent task completion from target logs. All three go on Fig. 6's axes. Write up the reclassifier's behaviour under evasion.

### Item 8 — P1.2 feature-evidence survey, P5.2 paper sections
- **P1.2:** `docs/feature_evidence.md` is a table of paper → feature set → reported accuracy → mapping to §4.3 feature index → caveat (e.g., the 2606.30119 network-only ceiling of 45–60%). Verify every number against the primary source. **Recommendation:** do this right after Task 0; it's cheap and supports the P2.4 choices.
- **P5.2:** ML methodology, classification and evasion results, ECH/SNI erosion limitation, and the approximations listed in `docs/feature_arithmetic.md` §7. Every number must trace to a config + seed (working agreement 5).

---

## Self-review notes

- Spec coverage: P2.4 → Tasks 0–4. P2.3 → Task 5. P3.2 (bulk reads, idempotent writes, policy versions, restart) → Tasks 6, 7, 9, 10. Items 3–8 → roadmap, each with DoD checks. §7 items cited: 7.1, 7.3, 7.4, 7.5, 7.6, 7.8, 7.9, 7.10, 7.11, 7.12, 7.14, 7.15, 7.20.
- Type consistency: `PacketObservation`/`FlowState`/`feature_vector`/`checkpoint_vectors` are consistent across Tasks 3–4. `execute`/`read_register` are used by both `push_policy` and `FakeBmv2`. `TreeEntry.match_ranges[0]` is the node key in Tasks 8 and 9.
