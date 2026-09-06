# Agent-Aware Networking — Task Plan (tasks.md)

> **Purpose of this file.** Divides `design.md` into phases and sub-tasks for **3 developers**
> working in parallel. Section numbers like §4.2 refer to `design.md`. Every task has a
> Definition of Done (DoD). The LLM system prompt all devs must use is in §"System prompt".

---

## Developer roles

| Dev | Codename | Owns | Primary directories |
|---|---|---|---|
| **Dev A** | DATAPLANE | P4 pipeline: parser, features, classifier wiring, QoS actions, PTF tests | `p4src/`, `tests/ptf/` |
| **Dev B** | CONTROL+ML | Ryu controller, punt handling, reclassification, ML training, tree compiler | `controller/`, `ml/` |
| **Dev C** | HARNESS+EVAL | Mininet topology, traffic corpus, baselines, experiment runner, metrics, dashboard, figures | `harness/`, `eval/`, `dashboard/` |

**Shared ownership:** `common/contracts.py` — changes require agreement of all three (a PR
touching it needs 2 approvals). Paper writing is shared; each dev drafts the sections covering
their subsystem.

**Integration rhythm:** short sync twice a week; every milestone (M1–M7, design.md §12) ends
with an integration day where all three branches merge and `make smoke` must pass on main.

---

## Phase 0 — Lock-in (Week 0) · all devs together

| ID | Task | DoD |
|---|---|---|
| P0.1 | Freeze one-liner, novelty axes, contributions C1–C4 | Written into requirements.md (done) |
| P0.2 | Create GitHub repo with layout from design.md §3; Makefile stubs; CI skeleton (p4c compile + pytest) | CI green on empty scaffolding |
| P0.3 | Write `common/contracts.py`: class labels, DSCP/queue map, feature order, register/table names, schemas (design.md §4) | All three devs sign off; file frozen |
| P0.4 | Pin toolchain versions (p4c, BMv2, Mininet, Ryu, Python 3.11) in `docs/ENVIRONMENT.md` + dev container (Linux VM required — Mininet/BMv2 do not run on macOS) | All three devs run `make dev-env` successfully |
| P0.5 | Commit the LLM system prompt (below) verbatim to both `AGENTS.md` (auto-read by Codex) and `CLAUDE.md` (auto-read by Claude Code) at repo root; keep the two files identical | Both files present; any edit updates both |
| P0.6 | Commit `ruff` config (format + lint) and add it to CI — mechanical style enforcement so mixed-vendor LLM output (Codex/Claude) stays uniform | `make lint` green; CI fails on violations |

## Phase 1 — Survey + related work (Weeks 1–2) · parallel with env setup

| ID | Owner | Task | DoD |
|---|---|---|---|
| P1.1 | Dev A | Related-work table, Stream B (pForest, Planter, Mousika, IIsy, NetBeacon, ACC-Turbo): read + summarize the tree→table compilation recipes we'll reuse | 1-page notes/paper each; compilation recipe chosen and documented |
| P1.2 | Dev B | Stream A (2606.20910, 2606.30119, 2602.09606, 2510.07176, 2605.14786, RFC 9421): extract exact feature sets + reported accuracies for our feature design | Feature-evidence table cross-referencing design.md §4.3 |
| P1.3 | Dev C | Stream C + DiffServ/QoS classics; verify remaining citations against primary sources; locate MAWI/CAIDA trace access + usage terms | All citations verified; traces downloaded |
| P1.4 | All | Draft paper Intro, Motivation, Related Work now (while fresh) | Sections compile in IEEE LaTeX template |
| P1.5 | All | `make dev-env` complete; **M1 skeleton**: l2fwd P4 + `choke_v1` topo + 1 iperf flow (A: l2fwd.p4, C: topology.py, B: controller connects and reads a register) | M1 demo on integration day |

## Phase 2 — Design freeze (Weeks 3–5)

| ID | Owner | Task | DoD |
|---|---|---|---|
| P2.1 | Dev A | Header/metadata structs + parser design incl. TLS-CH bounded parse and all fail-open paths (design.md §5.1, §7.15) | `headers.p4`, `parser.p4` compile; PTF parser corner-case tests written (may fail until P3) |
| P2.2 | Dev A | Feature-stage design: slot ownership, aging reset, saturating EWMA, sketch epochs (design.md §5.2, §7.1–7.3, §7.10–7.12) | Register map doc + pseudocode reviewed by Dev B |
| P2.3 | Dev B | Punt path + reclassifier design: cpu_header, bulk-read strategy, hysteresis K=3, override TTLs, torn-read guard (design.md §6.2–6.4, §7.4–7.8) | Sequence diagrams; agreed punt header in contracts.py |
| P2.4 | Dev B | Feature extraction spec: fixed-point arithmetic mirroring P4 bit-for-bit; checkpoint scheme (6/16/64 pkts) (design.md §8.2, §7.9) | Worked numeric examples that P4 (Dev A) and Python (Dev B) both reproduce |
| P2.5 | Dev C | Harness design: MCP target app, agent runner interface `(parallelism, think_time, task_script)`, label join logic (design.md §9, §7.20) | Runner interface doc; target container boots |
| P2.6 | Dev C | Baseline designs (4) + experiment config schema + burst-intensity preset definitions (design.md §10, §4.6) | Configs validate against schema; baseline approach reviewed |
| P2.7 | All | Architecture diagram (paper Fig. 1); finalize 3-class QoS policy values + protection preset numbers | Diagram in docs/; values in contracts.py |

## Phase 3 — Prototype (Weeks 6–10) · the heavy build

### Week 6–7 → Milestones M2 (features) + corpus start
| ID | Owner | Task | DoD |
|---|---|---|---|
| P3.1 | Dev A | Implement `features.p4`: registers, ownership/aging, EWMA, byte counters, first-8 sizes, CM sketch | PTF tests for §7.1, §7.3, §7.11, §7.12 pass |
| P3.2 | Dev B | `switch_api.py` (bulk register reads, idempotent writes, policy_version) + `policy.py` startup push | Controller kill/restart mid-traffic test passes (§7.5) |
| P3.3 | Dev B | `extract_features.py` per P2.4 spec + unit tests vs worked examples | Bit-exact match with Dev A's PTF feature dumps on shared pcap |
| P3.4 | Dev C | MCP target + 2 agent runners (Browser Use, Playwright-agent) + `capture.py` label pipeline | First labeled agent pcaps in repo LFS/storage |
| P3.5 | Dev C | Human traffic: MAWI/CAIDA replay wrapper + browsing capture sessions | Labeled human corpus available |

### Week 8 → Milestone M3 (tree) — **mid-project checkpoint: re-scan for competing papers**
| ID | Owner | Task | DoD |
|---|---|---|---|
| P3.6 | Dev B | `train.py` (depth ≤8, class weights, CV, path-coverage assert §7.14) + `compile_tree.py` + generated classifier.p4 section | Tree trained on real corpus; feature importances table produced |
| P3.7 | Dev B | `verify_equivalence.py` + wire into CI | 100% sklearn↔table agreement on held-out set; CI gate green |
| P3.8 | Dev C | Remaining 2 agent runners (AutoGen, Claude+MCP); grow corpus; corpus stats doc | ≥4 frameworks captured, label verification ≥95% |
| P3.9 | Dev A | Punt path in P4: clone-to-CPU, cpu_header, punt meter (§7.6) | PTF: punt flood capped; Dev B receives well-formed punts |

### Week 9 → Milestone M4 (classification live)
| ID | Owner | Task | DoD |
|---|---|---|---|
| P3.10 | Dev A | Wire classifier tables + warm-up rule + `tbl_flow_override` precedence into pipeline | Live replay through switch: labels match offline predictions |
| P3.11 | Dev B | `punt_handler.py` (ja4.py with test vectors, WBA verify, overrides w/ TTL) + `reclassifier.py` (sweep, hysteresis, torn-read guard, aging) | §7.4, §7.8 chaos tests pass; overrides observed to correct drifting flows |
| P3.12 | Dev C | `metrics.py`: percentile extraction, tool-call completion, per-class P/R from labels | Metrics validated on a hand-checked small pcap |

### Week 10 → Milestone M5 (QoS + centerpiece demo)
| ID | Owner | Task | DoD |
|---|---|---|---|
| P3.13 | Dev A | `qos.p4`: class actions, DSCP, priority queues, per-class ECN thresholds, meters (AGENT_INTERACTIVE never drops §7.16), congestion flag + hysteresis + protection preset flip (§5.5) | PTF: hysteresis behaves at HI/LO; ECN marks per class; meters enforce presets |
| P3.14 | Dev B | Protection preset values + normal preset install; tune meter rates with Dev C on real bursts | Presets in contracts.py; flip observed under induced congestion |
| P3.15 | Dev C | **Centerpiece demo scenario**: scripted agent storm + human video-call-like flow; live dashboard (per-class p50/p95/p99 + throughput) | Demo runs end-to-end: human p99 visibly flat with system ON |
| P3.16 | All | Integration day: `make smoke` covers full pipeline; tag `v0.5-prototype` | Smoke test in CI |

## Phase 4 — Evaluation (Weeks 11–13)

| ID | Owner | Task | DoD |
|---|---|---|---|
| P4.1 | Dev C | Implement 4 baselines as runnable configs; `run_experiment.py` with lockfile + config-hash output dirs (§7.19) | Each baseline runs the sanity config |
| P4.2 | Dev C | Full grid: agent-share × burst × {ours + 4 baselines} × ≥5 seeds; CIs in metrics | Raw results archived; anchor assertions evaluated (≥30% p99, <5% overhead) |
| P4.3 | Dev A | Overhead measurement (pipeline vs l2fwd, pinned cores §7.18) + table/register footprint report | Fig. 4 data + resource table |
| P4.4 | Dev A | State-scaling sweep 1k→100k flows; collision counter analysis (§7.1) | Fig. 5 data |
| P4.5 | Dev B | Evasion experiment: think-time-injecting agent runner variants; accuracy drop vs throughput self-penalty | Fig. 6 data; reclassifier behavior under evasion documented |
| P4.6 | Dev B | Classification P/R per class at line rate, per checkpoint (6/16/64 pkts) | Fig. 3 data |
| P4.7 | All | If anchors missed: tuning loop (tree retrain, ECN/meter/preset values) — budgeted, config-driven only, every run logged | Anchors hit or honest gap analysis written for paper |
| P4.8 | Dev C | `plots.py`: all figures regenerate deterministically from raw results | `make figures` produces every paper figure |

## Phase 5 — Paper + demo (Weeks 14–15)

| ID | Owner | Task | DoD |
|---|---|---|---|
| P5.1 | Dev A | Paper: design/implementation sections (pipeline, features, actions); limitations (BMv2, @atomic, DSCP domain scope) | Sections complete, numbers traceable |
| P5.2 | Dev B | Paper: ML methodology, classification + evasion results; ECH/SNI erosion limitation | Sections complete |
| P5.3 | Dev C | Paper: evaluation methodology, baseline results, figures; abstract + conclusion draft | Sections complete |
| P5.4 | All | Venue selection + formatting; internal red-team review pass (every claim vs every number) | Submission-ready PDF |
| P5.5 | Dev C | Presentation + live demo rehearsal (protection scenario, dashboard) | 2 successful full rehearsals |
| P5.6 | All | Repo cleanup: README, reproduction instructions, topology scripts, corpus documentation; tag `v1.0` | A stranger can rerun the sanity experiment from README alone |

---

## Dependency map (critical path)

```
P0.3 contracts ──► everything
P3.1 features.p4 ──► P3.3 extract (bit-exactness) ──► P3.6 train ──► P3.7 equivalence ──► P3.10 classify live
P3.4/P3.5/P3.8 corpus ──► P3.6 train
P3.10 + P3.13 ──► P3.15 centerpiece demo ──► Phase 4 grid
P4.2 grid ──► P4.8 figures ──► Phase 5 paper
```
If a dev is blocked on the critical path, the unblocked devs pull forward paper sections (P1.4
style) or test debt — never idle, never invent new scope.

---

## System prompt for developer LLMs (copy verbatim)

Every developer must give their LLM this system prompt (plus `requirements.md` and `design.md`
as context) when generating code for this project:

```text
You are a senior systems developer on "Agent-Aware Networking", a 3-developer research project
building an AI-agent traffic classifier + QoS system in P4/BMv2 with a Ryu control plane.
You have been given requirements.md and design.md. Treat design.md §4 (interface contracts) as
frozen law: never invent alternative names, values, schemas, or layouts for anything defined
there. All shared constants come from common/contracts.py — import them, never re-declare
literals.

STYLE & STRUCTURE
- Python 3.11. PEP 8, 100-char lines. Full type hints on every function signature. Google-style
  docstrings on public functions only. snake_case functions/vars, PascalCase classes,
  UPPER_SNAKE constants.
- Use dataclasses for structured data, pathlib for paths, logging (module-level logger, never
  print) with lazy %-formatting. f-strings elsewhere.
- Errors: fail fast with specific exceptions at trust boundaries (config parsing, switch API,
  pcap input); inside the data path prefer the documented fail-open behavior (UNKNOWN class,
  best-effort forwarding). Never use bare except. Never swallow an exception without logging it.
- No new third-party dependencies without team sign-off. Allowed baseline: ryu, scikit-learn,
  pandas, matplotlib, pyyaml, scapy, pytest.
- P4_16 (v1model): one concern per include file per design.md §3. Table/register names exactly
  as in design.md §4.2. Every read-modify-write register sequence goes inside an @atomic block.
  All arithmetic on features is saturating fixed-point (shifts only, no division except by
  powers of two). Every parser path must terminate in either a valid state or the documented
  fail-open path.
- P4 is a low-resource language for LLMs: generate P4 in small increments (one table, one
  control block at a time), assume the first draft has errors, and require the developer to run
  p4c and paste compiler errors back before continuing. Never emit large P4 files in one shot.
- Keep functions small and single-purpose. No speculative abstraction: no plugin systems,
  factory layers, config options, or extension points that design.md does not require.
  Build the simplest complete implementation of the task's DoD, nothing more.

CORRECTNESS DISCIPLINE
- This system ships to a real evaluation: account for the race conditions and edge cases in
  design.md §7. When your code touches flow state, slot ownership, epochs, policy versions, or
  the controller sweep, cite the relevant §7 item in a short comment stating the invariant
  being preserved (e.g. "// §7.11: reset all slot registers before claim").
- Fail-open is a hard rule: no code path may block, drop (except AGENT_BULK on meter red), or
  reject traffic due to classification uncertainty.
- Every task's code comes with tests per design.md §11: pytest for Python, PTF for P4. Tests
  for edge cases listed in the task's DoD are mandatory, not optional.
- Determinism: anything affecting experiments takes an explicit seed; experiment behavior is
  config-driven (eval/configs schema), never hardcoded.

INTEGRATION DISCIPLINE
- Before generating code, restate which design.md section and task ID (tasks.md) you are
  implementing and which contracts you consume/produce. If the task seems to require changing
  a frozen contract, STOP and say so instead of working around it.
- Match the existing repository layout (design.md §3). Do not create new top-level directories.
- Write commit-sized units: one task ID per PR, PR description lists the DoD items and how each
  is verified.
- When uncertain about a design decision not covered by design.md, choose the simplest option
  consistent with the fail-open rule and flag it explicitly as "DESIGN GAP:" in your response
  so it can be raised at the next team sync.
```

---

## Working agreements (humans, not LLMs)

1. **main is always green**: CI (compile + pytest + equivalence gate + smoke) must pass to merge.
2. **Contracts change process**: PR to `common/contracts.py` + design.md §4, 2 approvals, then
   announce in the team channel before merging.
3. **Lab notebook**: dated entries in `docs/notebook.md` for every design decision — feeds the
   paper's methodology section and viva answers.
4. **Week-8 checkpoint**: re-scan arXiv for competing papers (this space moved fast through
   2025–2026); finding one at week 8 is recoverable, at week 15 it is not.
5. **Numbers hygiene**: no number enters the paper unless it traces to an eval config + seed in
   the repo.
