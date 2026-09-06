# Agent-Aware Networking — Technical Design (design.md)

> **Purpose of this file.** The complete technical corpus for building the system. An LLM given
> this file plus `requirements.md` should be able to generate correct, integrable code for any
> module. Interface contracts in §4 are **frozen** — changing one requires updating this file
> first and notifying all three developers.

---

## 1. Goals, deliverables, outputs

### 1.1 Goals
- G1: Working prototype — P4/BMv2 pipeline that classifies agent traffic and applies QoS in one pass.
- G2: Reproducible four-baseline evaluation hitting **≥30% p99 human-latency reduction** and
  **<5% per-packet overhead**.
- G3: IEEE-format research paper claiming contributions C1–C4 (see requirements.md §4).
- G4: Live demo: agent burst hits the link, human p99 stays flat, dashboard shows it.

### 1.2 Deliverables (what must exist at the end)
| # | Deliverable | Acceptance |
|---|---|---|
| D1 | `p4src/` — complete P4₁₆ program | Compiles with p4c; passes PTF/unit tests; all FR-1..FR-6 |
| D2 | `controller/` — Ryu app | Installs policy, handles punts, runs reclassification; FR-7..FR-9 |
| D3 | `ml/` — training + tree→table compiler | Tree exported and loaded into switch; equivalence test passes (§8.3) |
| D4 | `harness/` — traffic generation + labeled corpus | ≥4 agent frameworks captured; human traces replayable; labels ≥95% verified |
| D5 | `eval/` — experiment runner + figures | One command reruns any experiment; all paper figures regenerate from raw data |
| D6 | Dashboard | Live per-class p50/p95/p99 + throughput during demo |
| D7 | Paper (IEEE LaTeX) + presentation + reproduction README | Compiles; all numbers traceable to eval outputs |

### 1.3 Outputs the paper needs (design everything to produce these)
- Fig. 1: architecture diagram. Fig. 2 (centerpiece): human p99 timeline during agent storm,
  system ON vs 4 baselines. Fig. 3: precision/recall per class. Fig. 4: overhead (per-packet
  latency CDF with/without pipeline). Fig. 5: accuracy & memory vs concurrent flows (1k→100k).
  Fig. 6: evasion — accuracy drop vs agent throughput self-penalty. Table: feature importances.

---

## 2. System architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ Mininet host machines                                                        │
│  h_agents[1..k]   h_human   h_bulk(iperf3)   h_target(MCP server+web app)   │
└──────┬───────────────┬───────────┬───────────────────▲──────────────────────┘
       │               │           │                   │
┌──────▼───────────────▼───────────▼───────────────────┴──────────────────────┐
│ BMv2 simple_switch  (P4 program: agent_aware.p4)                             │
│                                                                              │
│  INGRESS                                                                     │
│  ┌─────────┐  ┌──────────────┐  ┌───────────────┐  ┌──────────────────────┐ │
│  │ Parser   │→│ Flow-state    │→│ Classifier     │→│ Action stage          │ │
│  │ eth/ip/  │  │ registers +  │  │ (tree as      │  │ DSCP rewrite,        │ │
│  │ l4/TLS-CH│  │ CM sketch    │  │ range tables) │  │ queue select, meter, │ │
│  └─────────┘  └──────────────┘  └───────────────┘  │ punt-clone decision  │ │
│                                                     └──────────────────────┘ │
│  EGRESS                                                                      │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │ 3 priority queues · per-class ECN threshold (deq_qdepth) ·           │   │
│  │ congestion flag register (hysteresis) → human-protection mode        │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
└───────────────▲──────────────────────────────┬──────────────────────────────┘
                │ table writes (Thrift/CLI)     │ CPU-port punts (clone)
┌───────────────┴──────────────────────────────▼──────────────────────────────┐
│ Ryu controller (Python)                                                      │
│  policy.py: class→queue/DSCP/meter config, versioned, idempotent            │
│  punt_handler.py: full JA4 string, Web Bot Auth verify, exact-flow table    │
│  reclassifier.py: periodic register sweep → entropy-shift relabel + aging   │
└──────────────────────────────────────────────────────────────────────────────┘
                ▲ offline
┌───────────────┴──────────────────────────────────────────────────────────────┐
│ ml/: corpus → feature extraction (pcap) → sklearn tree ≤ depth 8             │
│      → compile_tree.py → table entries JSON → loaded by controller           │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Design rule:** the per-packet decision never leaves the switch. The controller *refines*
(exact-match overrides, reclassification) but the pipeline is always able to classify and act
alone. Controller down ⇒ system still works with the compiled tree (degraded, never broken).

---

## 3. Repository layout (frozen)

```
agent-aware-net/
├── p4src/
│   ├── agent_aware.p4          # top-level: includes + pipeline wiring
│   ├── headers.p4              # header/metadata struct definitions
│   ├── parser.p4               # FR-1
│   ├── features.p4             # FR-2, FR-3 (registers, sketch, EWMA)
│   ├── classifier.p4           # FR-4 (tree tables — GENERATED SECTION, see §8.3)
│   └── qos.p4                  # FR-5, FR-6 (DSCP, queues, meters, ECN, protection mode)
├── controller/
│   ├── app.py                  # Ryu entrypoint; wires modules below
│   ├── switch_api.py           # ONLY module that talks to BMv2 (Thrift/simple_switch_CLI)
│   ├── policy.py               # class policy install; versioned
│   ├── punt_handler.py         # CPU-port packets: JA4, WBA verify, exact overrides
│   ├── reclassifier.py         # periodic sweep: entropy-shift + aging + hysteresis
│   └── ja4.py                  # pure function: ClientHello bytes → JA4 string
├── ml/
│   ├── extract_features.py     # pcap + labels → feature table (schema §4.4)
│   ├── train.py                # sklearn tree, depth ≤ 8; exports model + report
│   ├── compile_tree.py         # tree → P4 table entries JSON (schema §4.5)
│   └── verify_equivalence.py   # sklearn predictions == P4 table predictions (§8.3)
├── harness/
│   ├── topology.py             # Mininet topo + BMv2 launch (single source of truth)
│   ├── mcp_target/             # instrumented target: small web app + real MCP server
│   ├── agents/                 # docker-compose + one runner script per framework
│   ├── human/                  # tcpreplay wrappers (MAWI/CAIDA), browsing capture notes
│   └── capture.py              # tcpdump orchestration + ground-truth label emission
├── eval/
│   ├── run_experiment.py       # config in → raw results out; the ONLY way to run experiments
│   ├── baselines/              # fifo.py, diffserv.py, fairq.py, app_limiter.py
│   ├── metrics.py              # pcap/log → percentiles, completion times, P/R
│   ├── plots.py                # raw results → paper figures (deterministic)
│   └── configs/                # YAML experiment configs (schema §4.6)
├── dashboard/                  # live demo dashboard (reads metrics stream)
├── common/
│   └── contracts.py            # ALL shared constants (class IDs, DSCP, queue IDs, ports, schemas)
├── tests/                      # pytest (Python) + PTF (P4)
├── docs/                       # architecture diagram sources, lab notebook
└── Makefile                    # make build / test / corpus / experiment / figures
```

---

## 4. Frozen interface contracts

These let three developers work in parallel. **Everything here mirrors `common/contracts.py` —
that file is the single machine-readable source of truth; this section is its documentation.**

### 4.1 Class labels and treatments

| Label | Value (2 bits) | DSCP | Queue (priority) | Meter | Notes |
|---|---|---|---|---|---|
| UNKNOWN | 0 | 0 (BE) | 1 (mid) | none | Default before warm-up; fail-open |
| HUMAN_INTERACTIVE | 1 | 34 (AF41) | 2 (highest) | none | Protected class |
| AGENT_INTERACTIVE | 2 | 18 (AF21) | 1 (mid) | meter M_AI | Never starved (NFR-3) |
| AGENT_BULK | 3 | 8 (CS1) | 0 (lowest) | meter M_AB | First shaped |

- BMv2 queues: `simple_switch` built with priority queueing; egress priority set via
  `standard_metadata.priority`. **BMv2 has no native WRR** — weighted shares are emulated with
  priority queues + per-class two-rate meters whose rates encode the share (stated honestly in
  the paper). Human-protection mode = tightened meter preset (see §6.4).
- ECN: per-class marking threshold on `deq_qdepth`: HUMAN th=48 pkts, AGENT_INTERACTIVE th=24,
  AGENT_BULK th=8 (defaults; tuned in Phase 4, but the *shape* — agents marked earlier — is fixed).

### 4.2 P4 ↔ controller interface

- **Tables (names frozen):** `tbl_tree_l0` … `tbl_tree_l7` (classifier levels),
  `tbl_flow_override` (exact 5-tuple → class, controller-installed),
  `tbl_class_action` (class → DSCP/queue/meter), `tbl_punt_filter`.
- **Registers (names frozen):** `reg_flow_key` (32b tag), `reg_last_ts`, `reg_iat_ewma`,
  `reg_iat_var_ewma`, `reg_pkt_count`, `reg_bytes_up`, `reg_bytes_down`, `reg_first_sizes`
  (N=8 slots/flow), `reg_flow_class`, `reg_cm_sketch_{0,1}` (two epochs), `reg_epoch_flag`,
  `reg_congestion_flag`, `reg_proto_meta` (packed TLS-CH fields).
- **Flow slot count:** `FLOW_SLOTS = 65536` (2^16, hash-indexed). Sketch: 4 rows × 4096 cols.
- **Punt format:** clone-to-CPU with custom header prepended:
  `cpu_header { flow_hash:16, class:8, reason:8, ingress_port:16, pad:16 }` then original packet.
  `reason`: 1 = first packet (TLS CH), 2 = classifier low-confidence, 3 = reserved.
- **Controller writes** go through `switch_api.py` only, are idempotent (write = upsert), and
  carry a monotonically increasing `policy_version` stored in a switch register so a restarted
  controller can detect stale state and re-push the full policy.

### 4.3 Feature vector (order frozen — tree compiler and P4 depend on it)

| Idx | Name | Type/units | Computed |
|---|---|---|---|
| 0 | iat_ewma_us | uint32, µs, EWMA α=1/8 | P4 |
| 1 | iat_var_ewma | uint32, EWMA of |Δ−mean| | P4 |
| 2 | pkt_count | uint32 | P4 |
| 3 | mean_pkt_size_up | uint16, bytes | P4 (bytes_up / pkt_count via shift approx) |
| 4 | updown_ratio_x100 | uint16, (bytes_up*100)/bytes_down, capped | P4 |
| 5 | first8_size_bucket | uint8, coarse signature of first-8 sizes | P4 |
| 6 | fanout_new_flows | uint16, CM-sketch estimate per src /epoch | P4 |
| 7 | tls_ext_count | uint8, from ClientHello (0 if none) | P4 parser |
| 8 | tls_alpn_class | uint8 enum {none, h1, h2, other} | P4 parser |
| 9 | flow_age_ms | uint32 | P4 |

All arithmetic is fixed-point integer (shifts, no division except by powers of 2). The sklearn
pipeline must be trained on **exactly these integerized features** extracted identically from
pcaps (`ml/extract_features.py` reimplements the P4 arithmetic bit-for-bit — this is a hard
requirement; see race/edge §7.9).

### 4.4 Corpus label format
`labels.csv`: `flow_id,src_ip,dst_ip,proto,src_port,dst_port,label,source_framework,pcap_file`
where `label ∈ {1,2,3}` (never 0 in training data). One row per flow. Produced only by
`harness/capture.py` (ground truth from orchestration, never hand-labeled).

### 4.5 Compiled tree format (`ml/compile_tree.py` output)
JSON: `{ "model_hash": ..., "feature_order": [...], "entries": [ {table, match_ranges, action,
action_params, priority} ] }`. Loaded by `controller/policy.py` at startup. The classifier tables
in `classifier.p4` are a generated section: `compile_tree.py` also emits the table *declarations*
so P4 and entries never drift.

### 4.6 Experiment config (YAML, `eval/configs/`)
```yaml
name: burst_sweep_v1
system: ours | fifo | diffserv | fairq | app_limiter
topology: choke_v1          # named topo in harness/topology.py
link_mbps: 20
agent_share_pct: [10,30,50,70,90]
burst_intensity: [low, med, high]   # defined in harness docs as flows/sec + parallelism
duration_s: 120
seeds: [1,2,3,4,5]
outputs: results/burst_sweep_v1/    # raw pcaps+logs; metrics.py consumes, never edits
```
Every number in the paper traces to a config file + seed.

---

## 5. Data-plane design (p4src/)

### 5.1 Parser (FR-1)
Ethernet → IPv4 → {TCP, UDP}. If TCP and payload begins `0x16 0x03` (TLS handshake) **and** this
is the flow's first observed payload packet, parse ClientHello shallowly: extension count, cipher
count, ALPN presence/class, SNI length bucket. Parsing is bounded (no loops over unbounded
extension lists — parse up to K=16 extensions, then stop; record `truncated` bit).
Non-IPv4, fragments with offset>0, or missing L4 → skip feature stage, class = UNKNOWN, forward
best-effort (fail-open, NFR-6).

### 5.2 Feature stage (FR-2, FR-3)
Per packet, in ingress:
1. `flow_hash = hash(5tuple) & (FLOW_SLOTS-1)`; `flow_tag = hash2(5tuple)` (different seed).
2. **Slot ownership check:** read `reg_flow_key[flow_hash]`. If ≠ `flow_tag`:
   - If `now − reg_last_ts[slot] > IDLE_TIMEOUT_US (5s)` → slot is stale: reset all per-flow
     registers for this slot, claim it (`reg_flow_key := flow_tag`), treat as new flow.
   - Else → **collision with a live flow**: do NOT touch the slot. Mark metadata
     `feature_valid=0`; classify via `tbl_flow_override` if present, else UNKNOWN. (§7.1)
3. Update: `Δ = now − last_ts`; skip EWMA update if `Δ == 0` (same-timestamp burst) or this is
   the flow's first packet. `iat_ewma += (Δ − iat_ewma) >> 3`; `iat_var_ewma += (|Δ − iat_ewma|
   − iat_var_ewma) >> 3`. Saturating adds everywhere (no wraparound corruption, §7.3).
4. Direction: ingress port tells us up vs down (topology contract: agents/humans on known ports).
   Update byte counters; record `pkt_size` into `reg_first_sizes` while `pkt_count < 8`.
5. Sketch: on new-flow claim only, increment CM sketch row counters for `src_ip` in the current
   epoch's sketch. Read = min over rows.
6. All register accesses that read-modify-write are wrapped in `@atomic` blocks (v1model executes
   them atomically per-packet on BMv2; the annotation documents the hardware requirement, §7.2).

### 5.3 Classifier stage (FR-4)
Warm-up rule: if `pkt_count < WARMUP_PKTS (6)` → class = UNKNOWN (mid queue) unless
`tbl_flow_override` hits. Else evaluate tree: each tree level is one table with range matches
over one feature (Mousika/IIsy recipe); label lands in `meta.class`. `reg_flow_class[slot]` is
updated so the controller can read the current label. `tbl_flow_override` (exact 5-tuple,
controller-installed) always wins over the tree — this is the reclassification hook.

### 5.4 Action stage (FR-5)
`tbl_class_action`: class → `{dscp, queue_priority, meter_id}`. Execute meter for agent classes;
meter RED result under congestion → mark ECN CE if ECT, else drop only AGENT_BULK (never drop
HUMAN/AGENT_INTERACTIVE on meter — shaping for those is queue priority only). DSCP rewrite on
IPv4 TOS. Punt decision: first packet of a claimed flow with TLS CH parsed, or low-confidence
leaf → clone to CPU **through `tbl_punt_filter` + punt meter** (global punt rate cap, §7.6).

### 5.5 Egress + human-protection mode (FR-6)
Egress reads `deq_qdepth`. Congestion flag with hysteresis: set when qdepth ≥ HI (64 pkts),
clear when ≤ LO (16 pkts) — two thresholds prevent flapping (§7.7). While flag set:
per-class ECN thresholds apply, and agent meters use the protective preset (controller
pre-installs both presets; the data plane flips between them via the flag — **no controller
round-trip in the congestion reaction path**).

---

## 6. Control-plane design (controller/)

### 6.1 Startup & policy (FR-7 prereq)
On connect: read `policy_version` register; if ≠ expected, push full policy (class actions, meter
presets, tree entries from `compiled_tree.json`, ECN thresholds), then write new version. All
writes idempotent upserts. Controller restart is therefore safe at any time (§7.5).

### 6.2 Punt handler (FR-7)
Parses `cpu_header`. reason=1: compute full JA4 from ClientHello bytes (`ja4.py`, pure function,
unit-tested against known vectors); check Web Bot Auth signature if headers visible (plaintext
deployments only); if JA4 strongly indicates non-browser stack, install
`tbl_flow_override(5tuple → AGENT_INTERACTIVE)` with TTL. Punts are best-effort: dropping a punt
loses refinement, never correctness (tree still classifies).

### 6.3 Reclassification loop (FR-8) — the entropy-shift mechanism
Every `T=2s`: bulk-read `reg_flow_class`, `reg_iat_ewma`, `reg_iat_var_ewma`, `reg_pkt_count`,
`reg_last_ts` (single Thrift bulk read per array — never per-slot loops).
For each active slot (last_ts fresh, pkt_count ≥ WARMUP):
- Recompute label from the *same* sklearn model (Python-side) on current stats.
- **Hysteresis:** require the new label to persist for `K=3` consecutive sweeps before
  installing/updating a `tbl_flow_override` entry (prevents class flapping, §7.8).
- **Snapshot consistency:** the sweep reads registers while the data plane writes them; a torn
  view across arrays is possible. Guard: re-read `reg_pkt_count` after the sweep; slots whose
  count advanced by > threshold during the read are skipped this round (eventual consistency is
  acceptable — a wrong class for one sweep degrades service slightly, never blocks; §7.4).

### 6.4 Aging & epoch management (FR-9)
Same sweep: slots with `now − last_ts > IDLE_TIMEOUT` get their `tbl_flow_override` entries
removed (TTL bookkeeping lives in the controller). CM sketch: controller flips `reg_epoch_flag`
every `EPOCH=1s` and zeroes the retired sketch — the data plane always increments the active
epoch and reads the active one, so a flip mid-packet reads at-most-one-epoch-stale data (§7.10).

### 6.5 Human-protection presets
Two meter presets installed at startup: `normal` (agent shares generous) and `protective`
(aggregate agent CIR ≈ 30% of link). The data-plane congestion flag selects between them
(pre-installed as two meter configs selected by flag bit in the action) — congestion reaction is
data-plane-local; the controller only *tunes* preset values between experiments.

---

## 7. Race conditions & edge cases (authoritative list — every one gets a test)

| # | Hazard | Design answer |
|---|---|---|
| 7.1 | **Flow-slot hash collision** — two live flows share a slot; features blend; garbage class | Slot ownership tag (`reg_flow_key`); collider gets `feature_valid=0` → UNKNOWN (fail-open), counted in `reg_collision_ctr` and reported in eval (state-scaling fig.) |
| 7.2 | **Register read-modify-write races** — concurrent pipeline threads on hardware | `@atomic` blocks around every RMW; BMv2 is per-packet sequential so tests pass, annotation preserves hardware correctness; documented in paper limitations |
| 7.3 | **Counter/timestamp wraparound** — 48-bit µs timestamp truncation, 32-bit byte counters on long flows | Saturating arithmetic on all feature counters; Δ computed in 48-bit then clamped; byte counters saturate at max (ratio still meaningful) |
| 7.4 | **Controller sweep reads torn state** — register arrays read while written | Skip slots whose pkt_count advanced during sweep; design tolerates stale reads (override is refinement, tree remains authoritative) |
| 7.5 | **Controller restart / duplicate policy push** | Idempotent upserts + `policy_version` register; full re-push safe at any time |
| 7.6 | **Punt storm** — SYN flood or agent burst → controller overload via clones | Data-plane punt meter (global cap, e.g. 100 punts/s); punts are refinements, dropping them is safe by design |
| 7.7 | **Congestion-flag flapping** at queue-depth boundary | Hysteresis (HI=64 set / LO=16 clear); flag is a single register bit written by egress only |
| 7.8 | **Class flapping** — flow oscillates near a tree threshold; reclassifier fights the tree | Override requires K=3 consecutive agreeing sweeps; overrides carry TTL; tree thresholds get ±ε guard bands at compile time for low-confidence leaves → punt reason=2 instead of hard label |
| 7.9 | **Train/serve skew** — sklearn floats vs P4 integer arithmetic disagree | `extract_features.py` reimplements P4 fixed-point math exactly; `verify_equivalence.py` gates CI: 100% label agreement on held-out set between sklearn tree and compiled tables (simulated) |
| 7.10 | **Sketch epoch flip race** — packet increments retired epoch | Two sketches + flag; at worst one packet lands in retired epoch (lost estimate ≤1); zeroing happens only after flip settles (one sweep delay) |
| 7.11 | **Stale slot inheritance** — new flow hashes into dead flow's slot, inherits EWMA | Idle-timeout check on ownership mismatch resets ALL slot registers before claim (single code path in `features.p4`, unit-tested) |
| 7.12 | **First-packet Δ garbage** — no previous timestamp | `pkt_count==1` skips IAT update; `Δ==0` (timestamp resolution) skipped too |
| 7.13 | **TCP retransmissions / reordering** distort IAT & size features | Accepted as noise (features are distributional, tree trained on real pcaps containing retransmits — same distortion at train and serve); noted in paper |
| 7.14 | **Asymmetric visibility** — only one direction crosses switch | updown_ratio degrades to capped max; tree must not have a leaf reachable *only* via ratio (training-time check in `train.py`: assert every path uses ≥1 timing/burst feature) |
| 7.15 | **Fragments / non-IPv4 / ICMP / malformed TLS CH** | Parser fail-open path: UNKNOWN class, best-effort forwarding, no register writes |
| 7.16 | **Meter starving AGENT_INTERACTIVE (NFR-3)** | AGENT_INTERACTIVE meter red action = ECN-mark only, never drop; only AGENT_BULK drops on red; completion-time metric enforces this in eval |
| 7.17 | **Agent evades via human-like pacing** | By design self-penalizing (slower = human-speed); measured in evasion experiment (accuracy drop AND agent throughput penalty, Fig. 6); reclassifier catches mid-flow behavior shifts |
| 7.18 | **BMv2 performance artifacts** — software switch jitter pollutes latency numbers | Links scaled to 10–50 Mbps so queuing, not CPU, dominates; overhead measured as *relative* (pipeline vs minimal l2fwd P4 program on same host); pinned CPU cores; stated in paper |
| 7.19 | **Concurrent experiment runs corrupt results** | `run_experiment.py` takes an exclusive lockfile per host; output dirs are append-only, named by config hash + seed |
| 7.20 | **Ground-truth label leakage** — labels derived from features we classify on | Labels come from orchestration only (which container generated the flow), never from traffic inspection |

---

## 8. ML workflow (ml/)

1. **Corpus** (Dev C produces, §D4): agent pcaps from ≥4 real frameworks (Browser Use, AutoGen,
   Claude+MCP tools, Playwright-driven browser) hitting the instrumented MCP target; human pcaps
   from MAWI/CAIDA replay + captured browsing; labels per §4.4.
2. **Extraction:** `extract_features.py` computes the frozen 10-feature vector per flow *at
   multiple packet-count checkpoints* (6, 16, 64 pkts) so the tree learns to classify early.
   Fixed-point arithmetic identical to P4 (shared constants from `common/contracts.py`).
3. **Training:** `train.py` — DecisionTreeClassifier, `max_depth=8`, class-weighted, stratified
   5-fold CV; export: model pickle, feature importances (paper table), per-class P/R, and the
   path-coverage assertion (§7.14).
4. **Compilation:** `compile_tree.py` walks the tree → range-match entries per level + generated
   table declarations (§4.5). Low-confidence leaves (purity < 0.85) compile to `punt reason=2`.
5. **Equivalence gate:** `verify_equivalence.py` runs held-out flows through (a) sklearn and
   (b) a Python simulation of the compiled tables; 100% agreement required before entries ship.

---

## 9. Traffic harness & corpus (harness/)

- **Target:** one container running a small HTTP app + a real MCP server (tools: search, fetch,
  compute) with request logging (ground-truth timing for tool-call completion metric).
- **Agent runners:** one script per framework, parameterized by `(parallelism, think_time=0,
  task_script)`; deterministic task lists per seed. Each runner logs `(container_ip, start, end)`
  → `capture.py` joins with pcap 5-tuples → `labels.csv`.
- **Human traffic:** tcpreplay of MAWI/CAIDA excerpts (timing-faithful, `--multiplier 1.0`) from
  `h_human`; plus ≥2h captured real browsing sessions (lab machines, consented, anonymized IPs)
  for labeled human flows. iperf3 from `h_bulk` as unlabeled background (excluded from P/R).
- **Bursts:** `burst_intensity` presets = (new flows/s, parallel connections, payload profile);
  defined once in `harness/README` and referenced by eval configs.

## 10. Evaluation design (eval/)

- **Topology `choke_v1`:** all senders → s1(BMv2) → bottleneck link (10–50 Mbps, configurable)
  → target. Latency measured end-to-end via embedded timestamps + tshark at both taps.
- **Baselines** implemented as alternative switch configs, same topology: FIFO (minimal P4
  l2fwd), DiffServ (static port-based DSCP + same queues), fair queuing (per-flow hashed round
  robin approximation), app-layer limiter (nginx rate limiting in front of target, FIFO switch).
- **Runs:** grid from config §4.6; each cell ≥5 seeds; metrics.py emits mean + 95% CI (t-dist).
  Anchor checks encoded as assertions: `p99_reduction ≥ 0.30`, `overhead < 0.05` — experiment
  reports FAIL loudly rather than silently shipping weak numbers.
- **Overhead measurement:** per-packet latency of full pipeline vs minimal l2fwd P4 on identical
  hardware/load (relative measure, §7.18); table entries and register bytes counted from the
  compiled artifacts.
- **State scaling:** synthetic flow generator sweeps 1k→100k concurrent flows; report collision
  rate (`reg_collision_ctr`), accuracy, memory.
- **Evasion:** agent runners with `think_time` drawn from measured human IAT distribution;
  report classifier accuracy AND agent task-completion slowdown on the same axis (Fig. 6).

## 11. Testing strategy (all devs)

| Level | Tool | What |
|---|---|---|
| Unit (Python) | pytest | ja4.py vectors; compile_tree round-trip; metrics math; extract_features vs hand-computed fixed-point cases |
| Unit (P4) | PTF on BMv2 | Parser corner cases (§7.15); slot ownership/aging (§7.1, §7.11); EWMA arithmetic; punt meter; congestion hysteresis |
| Property | pytest + sim | verify_equivalence.py (§8.5) — CI gate |
| Integration | Mininet smoke test | `make smoke`: tiny topo, 1 agent + 1 human flow, assert classes land in correct queues, DSCP set, dashboard receives data |
| System | run_experiment.py | Baseline sanity config (30s) in CI; full grid manually |
| Race/edge | targeted PTF + chaos script | Controller kill/restart mid-run (§7.5); punt flood (§7.6); collision stress (§7.1) |

CI (GitHub Actions): p4c compile + pytest + equivalence gate + smoke test on every PR.

## 12. Prototype build order (guide)

1. `make dev-env` — Mininet + BMv2 + p4c (pinned versions in `docs/ENVIRONMENT.md`; use the
   p4lang/p4app Docker image to avoid host toolchain drift).
2. Milestone **M1 (skeleton)**: l2fwd P4 + topology + one iperf flow end-to-end.
3. **M2 (features)**: feature registers live; controller dumps them; verified against tshark.
4. **M3 (corpus+tree)**: harness produces labeled pcaps; tree trained; equivalence gate green.
5. **M4 (classification)**: tree tables in pipeline; P/R measured on live replay.
6. **M5 (QoS)**: action stage + queues + protection mode; centerpiece demo works.
7. **M6 (baselines+grid)**: all four baselines runnable via config; full evaluation.
8. **M7 (paper+demo)**: figures regenerate from raw; dashboard polished.

Milestone → task mapping is in `tasks.md`.
