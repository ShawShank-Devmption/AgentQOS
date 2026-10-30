# Remaining System Implementation Plan

**Goal:** Complete the remaining implementation under docs/design.md, preserving frozen contracts
and separating executable evidence from external runtime/corpus prerequisites.

**Architecture:** Retain common/contracts.py and common/feature_math.py as the shared interfaces.
Extend controller/, harness/, and eval/ at their existing ownership boundaries. Add the data-plane
includes incrementally, with the developer compiler checkpoint mandated by AGENTS.md.

**Spec:** docs/requirements.md, docs/design.md, docs/tasks.md.

## Constraints and invariant trace

- Python 3.11; controller and its common imports remain Python 3.9 compatible.
- No new dependencies or top-level directories; preserve existing uncommitted work.
- Frozen contract changes require the team's approval process, not an implementation workaround.
- §7.1/7.11: validate ownership and reset/forget state on reuse.
- §7.4/7.8: skip torn snapshots and require three agreeing sweeps.
- §7.5/7.6: policy restarts and missing/overloaded punts must remain fail-open.
- §7.10: retire sketch epochs before clearing; do not erase an active epoch.
- §7.16: interactive traffic never drops due to classification or meter red.
- §7.19/7.20: exclusive experiments, append-only output, orchestration-only labels.

## Workstreams

### Controller — P3.11, design §6.2–6.4

Files: controller/ja4.py, controller/punt_handler.py, controller/reclassifier.py,
controller/switch_api.py, tests/test_ja4.py, tests/test_punt_handler.py,
tests/test_reclassifier.py, tests/test_switch_api.py.

Consume: frozen CPU header, feature order, canonical key math, CompiledTree, SwitchApi.
Produce: bounded punt intake, validated packet/flow identity, JA4 computation, pure reclassification
changes with ownership/torn-read/hysteresis/aging protection, safe epoch sequencing.

1. Write tests for malformed frames, GREASE/JA4 fields, bounded intake, class flapping, torn reads,
   slot reuse, aging, and epoch scheduling; run them and observe missing behavior.
2. Implement at the responsible module; do not install invented override action signatures or
   claim WBA verification without approved trust/key/signature dependencies.
3. Run targeted tests and lint; record external chaos/punt integration prerequisites.

### Evaluation — P3.12, P4.1/P4.2/P4.6/P4.8, design §10/§11

Files: eval/metrics.py, eval/plots.py, eval/run_experiment.py, eval/configs/,
tests/test_metrics.py, tests/test_experiment_execution.py, tests/test_plots.py.

Consume: ExperimentConfig, frozen labels/classes/checkpoints; raw measured observations.
Produce: percentile/throughput/completion/P-R summaries, 95% seed-level CIs, explicit anchor
checks, deterministic grid planning, locked append-only result directories and result plots.

1. Test hand-calculated metrics, absent/invalid data, non-finite observations, label leakage,
   repeated run prevention, failed-run metadata, deterministic config hashing and lock contention.
2. Implement measurement processing and execution bookkeeping without fabricated baseline results.
3. Add frozen-schema baseline configs; use explicit executable workload commands only where
   their runtime provenance can be preserved.
4. Run targeted tests and regenerate plots from a small hand-checked fixture.

### Linux harness — P1.5/P2.5/P3.4/P3.5, design §9/§11

Files: harness/runtime.py, harness/target_app/, harness/agent_runners/, harness/human_replay.py,
tests/test_runtime.py, tests/test_target_app.py, tests/test_human_replay.py.

Consume: topology HOSTS, BMv2 CLI, frozen capture labels and orchestration windows.
Produce: a launchable BMv2 Mininet network and M1 smoke command, real MCP HTTP target,
bounded deterministic task execution, faithful trace replay/capture boundaries.

1. Test subprocess failure/cleanup and prerequisite handling, MCP protocol validation and tools,
   path-safe trace handling and real replay command construction before implementation.
2. Keep framework adapters honest: no synthetic client labeled Browser Use/AutoGen/Claude.
3. Run host tests; clearly mark actual Linux and external-framework capture checks pending.

### Data plane — P2.1 first, design §3/§5.1/§7.15

Files: p4src/headers.p4, tests/ptf/.

1. Emit one header/metadata include consuming exact feature widths/order and CPU-header layout.
2. Supply a compile checkpoint and stop P4 generation until the developer runs p4c and shares output,
   as required by AGENTS.md. Do not emit a monolithic uncompiled pipeline.
3. Resume parser, features, punts, classifier wiring and QoS only after each compiler checkpoint.

### Integration — P0.6/P3.16/P5.6

Files: Makefile, .github/workflows/ci.yml, README.md, docs/notebook.md, docs/implementation_status.md.

1. Wire host test/lint/metrics gates and real Linux M1 smoke separately.
2. Compile top-level P4 programs only; include fragments are not standalone programs.
3. Review code across workstreams, run make lint and make test, and report verified coverage and
   each unresolved contract/runtime/data prerequisite accurately.

## Review focus

1. Missing packet/measurement data never becomes a zero-latency or successful classification claim.
2. Duplicate/reversed flow identities cannot double count labels or cross ownership boundaries.
3. Run failures retain manifests and clean owned resources without deleting other runs.
4. Epoch scheduling cannot clear an active sketch under rapid or delayed ticks.
5. Real framework/corpus and Linux evidence remain distinct from mocks, fixtures and proxy datasets.

## Continuation ledger — 28 October 2026

User requested completion and deeper browser-agent diagnosis after the implementation audit.
User approved proposed override action and payload-seen packing; see design §4.2 additions.
Existing design/plan remains the implementation authority; no alternate architecture is introduced.

- P3.11: implement handle-reconciled override writes through SwitchApi, bounded live CPU intake,
  serialized epoch/sweep service and an explicit daemon CLI; fail-open on refinement failures.
- P3.6/P4.6: add explicit orchestration group sidecars to training and diagnostics, retaining the
  frozen vector/schema. Investigate separability, session leakage and feature ablations; never
  infer ground truth or generic sessions from classifier features.
- P4.1: implement actual native runtime baselines where supported; preserve topology and record
  mechanisms. Do not label unavailable P4 queue behavior as a working DiffServ runtime.
- P2.1/P3.10: prepare one small P4 integration increment and its compiler checkpoint. Further
  P4 generation waits for developer-supplied p4c output, as AGENTS.md explicitly requires.
- P5.6: provide reproducible commands and evidence limits; retain external corpus/Linux gaps.

Ruling: extend the already approved design/plan, preserve the dirty working tree and do not
commit unrelated in-progress changes. Parallel work owns disjoint ML, diagnostics and baseline
files; root owns proposed shared contracts, controller integration, P4 checkpoint and final gate.

### Interrupted integration checkpoint

User approved running each p4c checkpoint locally rather than requiring manual compiler reports.
ML/session validation and diagnostics are implemented, with scoped verification recorded by their
owners. FIFO/fairq/app-limiter adapters are implemented; DiffServ still awaits its P4 adapter.
Controller receiver, daemon service, current-handle overrides, and punt-meter policy are
implemented; the last combined controller gate passed 34 targeted tests.

The P4 and workload implementation agents stopped with `workspace is out of credits`.
Saved P4 source contains the top-level forwarding program and partial feature stage; it does
not yet contain the full classifier/QoS/TLS/sketch pipeline. The saved `build/agent_aware.json`
is an incremental artifact, not proof that the current complete source has passed.
The workload agent saved `tests/test_workload.py` but did not save `eval/workload.py`,
`harness/traffic.py`, or the runtime JSON. Full pytest currently stops at that missing-module
collection error. Keep these test-first cases visible; do not suppress them to obtain a green gate.
Remaining integration is CPU-interface/controller network attachment, full P4 pipeline/DiffServ,
the built-in workload adapter, and actual Linux end-to-end smoke evidence.
