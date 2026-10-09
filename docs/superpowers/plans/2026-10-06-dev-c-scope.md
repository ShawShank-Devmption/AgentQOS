# Dev C Scope Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement
> this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the repository-owned Dev C traffic, evaluation, dashboard, and reproducibility
deliverables without changing the frozen cross-module contracts.

**Architecture:** Build upward from a deterministic Linux-only Mininet/BMv2 lifecycle, through an
instrumented MCP target and orchestration-derived traffic labels, into reproducible experiment
execution and offline/live metrics. Keep external programs behind small command boundaries so the
host test suite can verify commands, schemas, locking, and calculations without claiming Linux or
real-corpus evidence that was not actually produced.

**Tech Stack:** Python 3.11, Mininet, BMv2 `simple_switch`, Docker, Scapy, pandas, matplotlib,
PyYAML, pytest, standard-library HTTP and process APIs.

**Spec:** `requirements.md`, `design.md` sections 3, 4.4, 4.6, 7.18-7.20, 9-11, and `tasks.md`
tasks P1.5, P2.5, P2.6, P3.4, P3.5, P3.8, P3.12, P3.15, P4.1, P4.2, P4.8, P5.3, P5.5.

## Global Constraints

- Python 3.11, PEP 8, 100-character lines, full type hints, pathlib, dataclasses, and logging.
- Import shared constants from `common/contracts.py`; do not change design section 4 contracts.
- Add no dependency outside the approved baseline.
- All experiment behavior is config-driven and every stochastic operation takes an explicit seed.
- Ground truth comes only from orchestration metadata; never infer labels from packet features.
- Results directories are append-only and identified by config hash plus seed.
- Linux-only checks must fail with specific diagnostics on unsupported hosts.
- Do not claim real corpus, BMv2, latency, accuracy, or evaluation evidence without executing it.

## Review Focus

- Partial topology startup must clean up BMv2, Mininet, and temporary CLI state.
- Two concurrent experiments must not write the same host or run directory.
- Missing external commands, malformed logs, and corrupt pcaps must fail at trust boundaries.
- Empty or single-class metric inputs must return explicit undefined values or specific errors.
- Re-running a config/seed must reject the existing append-only output directory.

---

### Task 1: Executable `choke_v1` Topology (P1.5)

**Files:**
- Modify: `harness/topology.py`
- Modify: `Makefile`
- Test: `tests/test_topology.py`

**Interfaces:**
- Consumes: `topology_manifest(link_mbps: int)`, host MACs/ports, compiled BMv2 JSON path.
- Produces: `SwitchLaunchConfig`, `forwarding_commands()`, `run_smoke()`, and a CLI entry point.

- [x] Add failing tests for deterministic forwarding commands, launch validation, cleanup on an
  injected command failure, and an unsupported-host diagnostic.
- [x] Implement a Linux-only BMv2/Mininet lifecycle, table installation, ping/iperf smoke checks,
  and guaranteed cleanup.
- [x] Run `pytest tests/test_topology.py -q`, then the complete test suite and lint.
- [x] Add `make smoke-m1` without representing it as host-safe.

### Task 2: Instrumented MCP Target and Runner Contract (P2.5)

**Files:**
- Create: `harness/mcp_target/server.py`
- Create: `harness/mcp_target/Dockerfile`
- Create: `harness/mcp_target/README.md`
- Create: `harness/agents/base.py`
- Test: `tests/test_mcp_target.py`
- Test: `tests/test_agent_runner.py`

**Interfaces:**
- Consumes: JSON-RPC requests and explicit runner `(parallelism, think_time, task_script, seed)`.
- Produces: deterministic `search`, `fetch`, and `compute` responses plus JSONL request logs and
  orchestration windows consumable by `harness.capture.CaptureWindow`.

- [x] Add failing tests for each tool, invalid JSON-RPC, deterministic seeded tasks, parallelism,
  think time, and request-log timestamps.
- [x] Implement the standard-library HTTP target and runner protocol with fail-fast validation.
- [x] Add container/runtime documentation and run focused tests, suite, and lint.

### Task 3: Four Framework Adapters and Human Replay (P3.4, P3.5, P3.8)

**Files:**
- Create: `harness/agents/browser_use.py`
- Create: `harness/agents/playwright_agent.py`
- Create: `harness/agents/autogen.py`
- Create: `harness/agents/claude_mcp.py`
- Create: `harness/human/replay.py`
- Create: `harness/README.md`
- Modify: `harness/capture.py`
- Test: `tests/test_traffic_runners.py`
- Modify: `tests/test_capture.py`

**Interfaces:**
- Consumes: runner contract and trace path/rate/seed parameters.
- Produces: four source-framework identifiers, JSONL orchestration logs, replay commands, and
  verified `labels.csv` rows in design section 4.4 order.

- [x] Add failing tests for four adapters, deterministic task order, safe tcpreplay commands,
  orchestration-log parsing, reverse packets, ambiguous windows, and verification-rate reporting.
- [x] Implement thin adapters, replay orchestration, log-to-window parsing, and corpus statistics.
- [x] Document consent/anonymization, MAWI/CAIDA terms, and the low/med/high burst presets.
- [x] Run focused tests, suite, and lint.

### Task 4: Baseline Definitions and Run Matrix (P2.6, P4.1)

**Files:**
- Create: `eval/baselines/base.py`
- Create: `eval/baselines/fifo.py`
- Create: `eval/baselines/diffserv.py`
- Create: `eval/baselines/fairq.py`
- Create: `eval/baselines/app_limiter.py`
- Modify: `eval/run_experiment.py`
- Create: `eval/configs/burst_sweep_v1.yaml`
- Test: `tests/test_experiment_runner.py`

**Interfaces:**
- Consumes: `ExperimentConfig` and frozen system names.
- Produces: `RunSpec`, deterministic SHA-256 config hashes, exclusive host lock, append-only run
  directories, manifests, and baseline lifecycle commands.

- [x] Add failing tests for full grid expansion, stable hashes, exclusive locks, append-only paths,
  failure manifests, and each baseline command plan.
- [x] Implement baseline strategies and the runner with injected command execution for host tests.
- [x] Add a five-seed full-grid config and run focused tests, suite, and lint.

### Task 5: Metrics and Confidence Intervals (P3.12, P4.2)

**Files:**
- Create: `eval/metrics.py`
- Test: `tests/test_metrics.py`

**Interfaces:**
- Consumes: packet timestamps, tool-call logs, predicted/ground-truth labels, and per-run summaries.
- Produces: p50/p95/p99, completion times, per-class precision/recall, means, 95% t confidence
  intervals, and explicit anchor-check results.

- [x] Add hand-calculated failing tests for percentiles, paired completion times, confusion counts,
  absent classes, confidence intervals, p99 reduction, and overhead assertions.
- [x] Implement pure metric functions plus strict CSV/JSONL readers.
- [x] Run focused tests, suite, and lint.

### Task 6: Storm Scenario and Dashboard (P3.15)

**Files:**
- Create: `harness/demo.py`
- Create: `dashboard/server.py`
- Create: `dashboard/index.html`
- Test: `tests/test_demo.py`
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: topology lifecycle, runner specs, and metric snapshots.
- Produces: a seeded human-plus-agent storm command plan and a read-only live HTTP dashboard with
  per-class throughput and p50/p95/p99.

- [x] Add failing tests for storm ordering, system on/off selection, snapshot validation, and HTTP
  JSON/HTML responses.
- [x] Implement orchestration and a standard-library dashboard server with no hidden state changes.
- [x] Run focused tests, suite, and lint.

### Task 7: Deterministic Figures and Evaluation Documentation (P4.8, P5.3, P5.5)

**Files:**
- Create: `eval/plots.py`
- Create: `eval/README.md`
- Create: `docs/evaluation_methodology.md`
- Create: `docs/demo_rehearsal.md`
- Modify: `Makefile`
- Test: `tests/test_plots.py`

**Interfaces:**
- Consumes: immutable raw run summaries and metric aggregates.
- Produces: all planned paper figures with stable names and metadata plus exact reproduction and
  two-rehearsal checklists.

- [x] Add failing tests for deterministic ordering, required columns, stable filenames, and no raw
  input mutation.
- [x] Implement plotting, `make corpus`, `make experiment`, `make figures`, and documentation.
- [x] Run focused tests, suite, lint, and all host-safe validation targets.

### Task 8: Final Integration Evidence

**Files:**
- Modify: `docs/notebook.md`
- Modify: `docs/review_1_status.md` only if claims remain historically accurate.

**Interfaces:**
- Consumes: all prior task outputs.
- Produces: a truthful host verification record and a Linux/real-corpus execution checklist.

- [x] Run `make lint`, `make test`, config validation, and deterministic dry-run commands.
- [ ] Run Linux M1/corpus/evaluation steps if the required VM and external data are available.
- [x] Record verified evidence separately from unexecuted external-runtime work.
- [x] Perform a final code review and fix all important findings with regression tests.
