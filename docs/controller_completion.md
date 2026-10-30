# Controller completion evidence — P3.11

Implemented the host-testable core of design §6.2–6.4, using frozen contracts from
`common/contracts.py`, canonical hash/feature math from `common/feature_math.py`, and the same
`common/compiled_tree.py` evaluator used by equivalence checks. No new dependencies or wire
contracts were added. This does **not** establish the live P3.11 DoD: observed switch overrides,
Linux punt reception, and kill/restart chaos evidence still require the decisions and runtime
checks below.

## Integration API

- `ja4.ja4(tls_record_bytes) -> str`: consumes one complete first ClientHello record and raises
  `ClientHelloError` for unsupported/truncated structure. Follows the
  [FoxIO JA4 specification](https://github.com/FoxIO-LLC/ja4/blob/main/technical_details/JA4.md),
  retrieved 2026-10-28. Tests reproduce its complete published
  `t13d1516h2_8daaf6152771_e5627efa2ab1` example, plus hand-calculated variants, GREASE removal,
  signature ordering, binary/empty ALPN normalization, and nested length failures.
- `BoundedPuntQueue(capacity).offer(cpu_clone) -> bool` and `.pop() -> bytes | None`: both
  nonblocking; overload increments `.dropped` and drops only the newest CPU clone (§7.6).
- `PuntHandler(upstream_ports, downstream_ports, agent_fingerprints=frozenset()).handle(frame)`:
  returns `FlowObservation` or `None` on malformed/unsupported frames. The caller supplies
  topology ports from `harness.topology.HOSTS`; the controller does not import the Linux harness.
  Uses the frozen eight-byte CPU header and validates IPv4/TCP/UDP lengths, direction,
  canonical slot hash, and class encoding. Ethernet padding is excluded from payload parsing.
  IP fragments are ignored conservatively; no reassembly is attempted.
- `FlowIdentity(client_ip, server_ip, proto, client_port, server_port)` stores numeric endpoint
  fields, with `.key` and `.tag` from common canonical math. Upstream and downstream packets
  yield the same identity. `parse_punt(frame, upstream_ports, downstream_ports)` exposes the
  strict parser separately and raises `PuntParseError`; the handler logs and fails open.
- `Reclassifier(compiled_tree).observe(observation)` remembers an identity provisionally.
  `sweep(collect_snapshot(api)) -> tuple[OverrideChange, ...]` produces semantic exact-flow
  `upsert`/`delete` intents, each with `.flow` and `.traffic_class`. It does not send guessed
  override commands. `acknowledge(successful_changes)` records writes actually applied by a
  future approved adapter. Unacknowledged desired writes are emitted again; startup policy
  already clears stale overrides on process restart (§7.5).
- `collect_snapshot(api)` performs **one CLI session** with ordered bulk reads, preserving
  packet-count and epoch brackets. `RegisterSnapshot.features(slot, flow)` reconstructs the
  exact frozen feature order, including flattened first-size arrays and client-source sketch
  indices. Snapshots own immutable copies of their register arrays.
- `EpochScheduler(api).tick(monotonic_microseconds) -> bool` flips at most once per second.
  The first transition only flips; subsequent transitions reset a known retired sketch after
  a full interval **before** reactivating it (§7.10). Rapid calls do nothing, delayed calls do
  one transition, and unexpected flags/partial failures discard retirement knowledge rather
  than reset an uncertain array. One scheduler must own the switch epoch flag.
- Additions to `SwitchApi`: `read_registers_ordered(names)` preserves duplicate array reads,
  and `register_reset_command(name)` validates the frozen register name. Existing APIs and
  their return types are unchanged. All switch I/O remains inside `switch_api.py`.

## Sweep invariants

Ownership tags are checked before evidence or classification (§7.1/7.11). A punt naming an
occupied slot cannot evict the known owner's evidence until the switch confirms the new tag;
at most one provisional replacement is held per slot. Reused slots forget
old evidence, pending candidates and desired overrides. A changed claim timestamp also resets
state when the same five-tuple reappears as a fresh generation.

A packet count regression or an advance greater than eight skips classification and breaks the
candidate streak (§7.4). Invalid cells or an epoch change also break the streak. Classification
uses common saturating arithmetic and compiled-tree walking; no sklearn dependency is imported
by the controller (§7.9). Warm-up snapshots do not contribute to a candidate streak. Three
consecutive usable, agreeing sweeps are required before behavioral overrides (§7.8).

JA4 evidence requires an explicitly configured fingerprint allowlist, is trusted only after
ownership validation, and takes precedence over behavioral candidates until aging. No default
allowlist guesses which clients are agents. A fingerprint is not cryptographic identity: real
browser agents can match human browsers, clients can imitate fingerprints, and ECH can hide SNI.
JA4 here supports TCP/TLS only, not QUIC, DTLS, SSLv2 framing, split TCP handshakes, or TLS records
requiring reassembly. Truncated ClientHello evidence is unavailable; flow tracking still works.

Idle timeout bookkeeping follows provisional G9: after initial activity is observed, three
unchanged two-second sweeps expire the flow and delete its override. Last packet timestamp joins
the packet count in the activity marker so saturated counters cannot age out active flows.
No register resets/reclaims are attempted by the reclassifier; those remain atomic in P4.

## Remaining decisions and external evidence

- **DESIGN GAP — frozen override ABI:** design §4.2 freezes the table name and exact five-tuple
  meaning but does not specify its P4 action name, parameter signature, or handle management.
  No override writer or pretend live integration was created. Approve the ABI in the frozen
  contract first; then an adapter can apply/acknowledge these intents through SwitchApi,
  reconcile handles for idempotent upserts/deletes, and recover from partial CLI batches.
- **DESIGN GAP — provisional controller choices:** G9 (three unchanged sweeps), G10 (eight-packet
  torn-read tolerance), and G11 (sticky evidence) come from `docs/controller_design.md` and
  still need the documented sync confirmation. Epoch reads add both sketch arrays and an
  epoch bracket for safety; measure bulk-read cost on the VM. If it exceeds the 1.5 s budget,
  approve the previously documented Thrift-client migration.
- **Web Bot Auth is not implemented.** G12 requires approved Ed25519 verification dependencies,
  a trusted key source and key rotation/replay policy, plus visible HTTP signature material.
  Merely seeing an HTTP header is not verification. No unverified header produces evidence.
- `controller/app.py` remains the startup policy entrypoint. Wire the bounded AF_PACKET reader,
  serialized two-second sweeps and one-second epoch ticks only after the CPU interface/clone
  delivery and override ABI are agreed. The host core has no background threads or raw sockets.
- Run Python 3.9 tests in the Ryu environment; this host has only Python 3.11. Source syntax is
  checked with the Python 3.9 AST grammar, which is not equivalent to executing under 3.9.
- Run P3.9/PTF clone-format and punt-flood tests, BMv2 CRC/hash fixture verification, partial
  override-write/restart chaos, and observed drifting-flow corrections in the Linux VM.

## Verification

Tests were written before the missing implementation, and the initial run failed on missing
controller modules/APIs. Follow-up malformed-SNI/port and empty-ALPN tests were also observed
failing before their fixes.

Host command: `.venv/bin/pytest tests/test_ja4.py tests/test_punt_handler.py
tests/test_reclassifier.py tests/test_switch_api.py` (77 passing at this checkpoint).
Scoped `ruff check` and `ruff format --check` pass for all eight controller/test files.

A full repository pytest run at the earlier 74-test checkpoint had 289 passing and two sandbox
socket-bind failures: `test_real_mcp_tasks_emit_capture_window_and_completion_log` and
`test_http_target_and_append_only_request_log`. The final `make lint` rerun passes across the
repository (71 Python files formatted at this checkpoint), and `git diff --check` passes.
Integration owns the final full-suite/runtime rerun after all concurrent workstreams finish.
