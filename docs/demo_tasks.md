# Demo and evaluation plan — tasks (delegation-ready) and paper plan

Status date: 2026-10-31. Spec authority: `docs/requirements.md`, `docs/design.md` (§4 frozen),
`docs/tasks.md`. This document adds the remaining work for the centerpiece demo (P3.14/P3.15/P5.5)
and the live-gateway demo, and the evaluation plan that the paper will report.

Ownership: **I-tasks (gcloud, VM networking) → Shashank, who is also Dev B (CONTROL+ML) and keeps
Dev B's code tasks. C/D/G-tasks (repository code) → Dev A/B/C, executed with Claude Opus 5.5.** Each code task below is written as a self-contained brief:
context, files, contracts consumed, definition of done, verification command. The agent executing
a task must read `CLAUDE.md`, restate the design.md section and task ID before coding, and stop on
any frozen-contract conflict (DESIGN GAP) instead of working around it.

## 0. Verified state (2026-10-31)

Host (macOS): 485 pytest passed, `make lint` green, all work committed on `dev-b/p3.6-tree`
(`47c6023`).

Linux VM `agent-qos-p4dev` (GCP `asia-south2-a`, project `vivid-rush-513007-k5`, Ubuntu 22.04,
p4c + BMv2 with priority queueing + Mininet + PTF + Python 3.11/3.9; repo at `~/cn_project`):

| Gate | Result |
|---|---|
| `make lint` | green |
| `make test` | 484 passed; 3 PTF modules collected by plain pytest and fail without a switch (→ C0) |
| `make check-py39` | 153 passed (Ryu venv, Python 3.9) |
| `make build` | `l2fwd`, `agent_aware`, `diffserv` compile with real `p4c-bm2-ss` |
| PTF `test_diffserv` | **passed on real BMv2** |
| PTF `test_parser` | harness error: Linux AF_PACKET rejects the `short_ethernet` (<14 B) frame, `EINVAL` (→ C2) |
| PTF `test_agent_pipeline` | fails at `reg_cm_sketch_0[row,col] == 1`: reads 2 after one ClientHello (→ C3) |
| M1 Mininet smoke (`l2fwd`) | **passed**: 18.8 Mbps through the 20 Mbps `choke_v1` link |
| `review_1_smoke` ours / fifo / diffserv | **all three `completed.json: success`** on real BMv2 (seed 1, 30 % agent share, low burst, 20 Mbps, 30 s) |

First measured numbers (`results/linux-live/workload-*/…/summary.json` on the VM):

| System | HUMAN p50 / p95 / p99 (ms) | AGENT_BULK p99 (ms) | HUMAN Mbps | Live classification |
|---|---|---|---|---|
| ours | 503 / 573 / **601** | 572 | 10.5 | HUMAN recall 1.0, precision 0.014; AGENT_INTERACTIVE recall 0; AGENT_BULK recall 0.38–0.45 |
| fifo | 110 / 111 / **112** | 112 | 14.0 | n/a |
| diffserv | 110 / 111 / **111** | 111 | 13.8 | port rule → HUMAN for all |

Two conclusions drive the task list:

1. **Overhead, not queueing, dominates "ours".** Latency is identical across classes within each
   system, and ours adds ~490 ms over FIFO at the same queue rate. The software switch cannot
   process `agent_aware.p4` at ~2.9 kpps in this configuration. The anchor (≥30 % p99
   reduction, <5 % overhead) is currently missed by a wide margin. Until C4 is resolved, no QoS
   claim can be made.
2. **Live classification collapsed to HUMAN.** The UDP stress workload is not the training
   shape; the tree labels almost everything HUMAN, so no differentiation occurs. The demo
   traffic must match a trained profile (D2) and be verified live (D3) before QoS can show.

## 1. Infrastructure tasks — owner: Shashank

| ID | Task | Depends on | Definition of done |
|---|---|---|---|
| I1 | **VM cost and lifecycle.** The VM is an `e2-standard-16` (≈ $0.78/h in Delhi, ≈ $19/day) running since 28 Oct (est. ≈ $50 so far; exact figure in Console → Billing → Reports). Stop it when idle. Resize to `e2-standard-4` (`gcloud compute instances stop` → `set-machine-type --machine-type e2-standard-4` → `start`; ≈ $0.19/h, adequate for a 10 Mbps BMv2 demo; pin the switch to one core for overhead runs per §7.18). Promote `34.131.42.32` to a static address. Enable `billingbudgets.googleapis.com` and set a budget alert. Firewall: UDP 51820 from anywhere, SSH from own IP only. | — | `describe` shows `e2-standard-4` + static address; budget alert received; VM stopped outside work sessions. |
| I2 | **WireGuard server on the VM.** `apt install wireguard qrencode`; `wg0` per §3; `net.ipv4.ip_forward=1`; MASQUERADE to `ens4`; scripts `harness/gateway/add_device.sh` (key pair, profile, QR) and `revoke_device.sh`. Test from the Android phone over its own hotspot. | I1 | Phone with profile: `curl ifconfig.me` returns the VM IP; a laptop onboarded via QR does the same; scripts committed. |
| I3 | **Netns/veth plumbing and bypass.** `harness/gateway/netsetup.sh` creates netns `lan`, moves `wg0` in, creates `gw-lan`/`gw-lan-sw` and `gw-wan`/`gw-wan-sw` veth pairs with the §3 addresses/routes/NAT, disables offloads and IPv6 on the `-sw` ends; `teardown.sh` reverses; `bypass.sh` bridges `gw-lan-sw`↔`gw-wan-sw` directly for when `simple_switch` is down. | I2 | With bypass active a tunnelled phone browses; `tcpdump -i gw-lan-sw` shows client packets; teardown returns to the I2 state. |
| I4 | **Mac access and sync.** Aliases for VM start/stop, dashboard tunnel (`gcloud compute ssh agent-qos-p4dev --zone asia-south2-a -- -L 8080:localhost:8080`), and code sync (`git archive HEAD \| gzip \| gcloud compute ssh … 'tar -xz -C ~/cn_project'`, or push the branch and `git pull`). Documented in `docs/demo_runbook.md`. | I1 | One command each for start, sync, tunnel, stop. |
| I5 | **Demo-day devices.** Android phone as hotspot with a WireGuard profile (video call); team laptop with a profile (agent bulk + MCP-tool agent); printed QR card with the G7 consent text. | I2, G7 | Dry run: two devices through the tunnel while the dashboard is open on the Mac. |
| I6 | **Evidence archive.** After each VM run, copy `results/linux-live`, `results/linux-ptf` and later run directories off the VM (append-only, §7.19) to the team's shared storage; never into git. | — | Archive index with run directory, config hash, commit, date. |

## 2. Code tasks — owners: Dev A / B / C (execute with Claude Opus 5.5)

Priority order: **C4 and D2 first** (they gate every QoS claim), then C0–C3, D3–D5, then G-tasks.

### 2.1 Fixes exposed by the Linux run

**C0 — PTF modules collected by plain pytest** · Dev C · *tasks P3.16*
Context: on hosts where `ptf` is installed, `pytest` imports `tests/ptf/*` and they fail with
`KeyError` on test params. Files: `tests/conftest.py`, `tests/ptf/`. DoD: `make test` green on
the VM; suites still run via `ptf --test-dir tests/ptf`. Verify: `make test` on the VM.

**C2 — PTF parser `short_ethernet` case** · Dev A · *design §5.1, §7.15*
Context: Linux AF_PACKET returns `EINVAL` for frames shorter than 14 bytes, so the case aborts the
whole suite before any assertion. Files: `tests/ptf/test_parser.py`. DoD: the case either builds a
14-byte minimum frame with a truncated IPv4 header (still exercising the fail-open path) or is
skipped with the OS reason logged; the other 20+ cases run and pass on the VM. Verify:
`results/linux-ptf/ptf-parser.log` shows `OK`.

**C3 — Sketch double increment** · Dev A · *design §5.2, §7.10, §7.12*
Context: PTF reads `reg_cm_sketch_0[row·COLS+col] == 2` after one ClientHello on real BMv2 (an
earlier container check read 1). Invariant: one increment per packet per row per epoch. Hypotheses:
the sketch update runs twice on the same packet (ingress + clone/resubmit path), or the test
sends two frames before reading. Files: `p4src/features.p4`, `p4src/agent_aware.p4`,
`tests/ptf/test_agent_pipeline.py`. DoD: root cause written in `docs/notebook.md`; fix in the
responsible file with a `// §7.10` comment; PTF suite passes on the VM. Verify:
`results/linux-ptf/ptf-agent.log` shows `OK`.

**C4 — Pipeline overhead (P4.3, blocks every QoS claim)** · Dev A, Dev B assists · *design §10, §7.18*
Context: `ours` adds ≈ 490 ms over FIFO at the same offered load and queue rate, uniformly across
classes. Hypotheses, in order: (a) `simple_switch` built or started with per-packet logging
(`--log-console`/debug build); (b) clone-to-CPU volume: `tbl_punt_filter`/`M_PUNT` should cap
punts at `PUNT_RATE_PPS`; verify punt counters versus packets; (c) intrinsic cost of eight
range-table lookups + registers + sketch per packet on one BMv2 thread. Method: run `l2fwd` vs
`agent_aware` at fixed pps on `choke_v1`, switch pinned to one core, measure one-way latency and
switch CPU; record punt counters. Files: `harness/runtime.py` (launch flags), `p4src/`, new
`eval/configs/overhead_*.yaml`. DoD: a table `pps × {l2fwd, agent_aware} → p50/p99, CPU %`
in `docs/notebook.md`; the fix for (a)/(b) if confirmed; if (c), the maximum pps at which added
p99 < 5 % and the demo link rate set below it. Verify: rerun `review_1_smoke` ours vs fifo;
HUMAN p99 ratio reported.

**C1 — Contracts PR: `live_gateway` topology** · Dev A proposes, all approve · *design §4.2, §4.6*
Context: `SENDER_SWITCH_PORTS=(1,2,3)`, `TARGET_SWITCH_PORT=4`, and queue shaping only on port 4
(upload for gateway clients). Download toward clients is the congested direction on a hotspot.
Files: `common/contracts.py`, `docs/design.md` §4, `harness/topology.py`. DoD: a `live_gateway`
topology with LAN/WAN port map and shaping/congestion handling on both ports; `choke_v1`
behaviour unchanged; `tests/test_contracts.py` extended. Verify: `make test`, `make build`.

### 2.2 Lab demo (Mininet on the VM)

**D1 — Linux evidence loop** · Dev C
Runner: from `~/cn_project`,
`sudo env PATH=$PATH HOME=$HOME PYTHONPATH=$PWD .venv/bin/python ~/agentqos-run-live.py`
(writes `results/linux-live/`; move the script into `harness/` as `linux_evidence.py` with tests
for its command construction). DoD: `outcomes.json` all zero after C0/C2/C3; every workload dir has
`completed.json: success`; archived per I6.

**D2 — Demo traffic profile** · Dev C · *design §9, §4.6*
Context: `eval/workload.py` sends constant-rate 1024-byte UDP for every class; the tree never saw
that shape. Files: new profile JSON next to `eval/configs/workload_runtime.json`, `eval/workload.py`
`build_plan`, `harness/traffic.py`, `tests/test_workload.py`. DoD: config-driven video-call human
flow (cadence and sizes matching `harness/synthetic_traffic._human_video_call`), agent-bulk storm
(matching `_agent_ingest`/`_agent_crawler`), MCP-tool agent lane (matching `_agent_mcp`), all seeded,
no literals outside the profile; `PROFILE_FIELDS` extended rather than a parallel loader. Verify:
pytest: deterministic schedule per seed, per-class offered load within 2 % of config, packet IDs
unique; `review_1_smoke` still runs on the VM with the new profile.

**D3 — Live classification check** · Dev B · *design §8, §7.9, §7.20*
Context: live run labelled nearly everything HUMAN. Files: `eval/pcap_metrics.py` output,
`ml/train.py`, `ml/compile_tree.py`, `ml/verify_equivalence.py`. DoD: per-flow prediction table
for a D2 run at 6/16/64 packets from `observations.json`; if the call is not HUMAN or the storm is
not AGENT_BULK by checkpoint 16, add the D2 shapes to the synthetic generator, retrain, equivalence
gate 100 %, re-run. Overrides must not be used to force labels. Verify: classification block of
`summary.json` on the VM; `results/<run>/model/report.json`.

**D4 — QoS under congestion** · Dev A + Dev B · *design §5.5, §7.7, §7.16; tasks P3.13/P3.14*
DoD: PTF or live log showing `reg_congestion_flag` set at HI and cleared at LO; protective preset
in effect; AGENT_BULK red drops counted; HUMAN/AGENT_INTERACTIVE meter drops = 0; `demo_call_ours`
HUMAN p99 versus `demo_call_fifo` with the target stated before the run; any retuning of
`CONGESTION_*_PACKETS`/`METER_PRESETS` via a contracts PR, each run logged. Depends on C4, D3.

**D5 — Live dashboard feed** · Dev C · *design D6*
Context: dashboard reads `observations.json`/`summary.json`, written only at run end. Files:
`eval/metrics.py` (reuse percentile code), new `eval/live_snapshot.py`, `dashboard/server.py`,
`tests/test_dashboard.py`. DoD: rolling per-class p50/p95/p99 + throughput snapshot about once a
second, replaced atomically (`os.replace`), consumed by `dashboard.server`; FIFO-vs-ours view;
empty class = unavailable, never zero. Same writer reused by G4. Verify: pytest; dashboard updates
during a 30 s VM run through the SSH tunnel.

**D6 — Optional real video stream** · Dev C
ffmpeg/GStreamer RTP from the human host, played at the receiver. DESIGN GAP: ffmpeg is outside
the baseline tool list; needs team sign-off before starting.

**D7 — Demo runner and runbook** · Dev C, all rehearse · *tasks P5.5*
One command: `demo_call_fifo` then `demo_call_ours` (single seed), dashboard started, outputs
append-only; `docs/demo_runbook.md` shared with I4. DoD: two full rehearsals from the runbook
alone; screen recording saved. Depends on D3, D4, D5.

### 2.3 Live gateway (real devices through the VM)

```
devices on hotspot ══ WireGuard UDP 51820 ══► wg0 10.42.0.1/24 (netns lan)
  gw-lan ══ gw-lan-sw [BMv2 LAN] ─ agent_aware.p4 ─ [BMv2 WAN] gw-wan-sw ══ gw-wan ── NAT ── ens4
  set_queue_rate ≈10 Mbps on both ports; controller daemon on gw-cpu (port 255)
  header-only taps on gw-lan-sw / gw-wan-sw → snapshot.json → dashboard.server 127.0.0.1:8080
Mac: SSH tunnel → http://localhost:8080
```

**G2 — Inline switch launcher** · Dev A · depends on I3, C1
Files: new `harness/gateway.py`, tests. DoD: starts `simple_switch` on the §3 interfaces with the
chosen pipeline and `--priority-queues`; static `tbl_l2_forward` entries for the two inner MACs; a
multicast group so broadcast/unknown never egresses its ingress port; `set_queue_rate`/
`set_queue_depth` on both ports from the experiment config; `fifo`/`ours` swap; health check that
invokes `bypass.sh` when the switch dies. Verify: tunnelled iperf3 capped at the configured rate;
kill `simple_switch` → connectivity back in < 5 s; pytest for command construction.

**G3 — Controller live** · Dev B · depends on G2
DoD: policy push with the compiled tree, CPU port `gw-cpu`, punts from real ClientHellos, sweeps,
overrides; controller restart mid-traffic re-pushes policy without traffic loss (§7.5). Verify:
controller log on the VM.

**G4 — Live measurement feed** · Dev C · depends on G2, D5
DoD: `tcpdump -s 96` on both `-sw` interfaces; packets matched with the `eval/pcap_metrics.py`
identity; ambiguous duplicates (TCP retransmits) skipped, not fatal; class from egress DSCP; D5
writer; no payload bytes on disk. Verify: dashboard on the Mac updates from phone traffic; pytest.

**G5 — Live scenario** · Dev B + Dev C, Shashank operates devices · depends on G3, G4, D4, I5
Phone video call + laptop agent bulk job + MCP-tool agent through the tunnel; FIFO then ours on the
same VM. DoD: recorded side-by-side runs; call p99 under both; bulk throughput metered; hairpin RTT
reported separately.

**G6 — Accuracy on labelled team devices** · Dev B · depends on G3
Orchestration-labelled captures from team devices feed the real corpus (P3.4/P3.8); retrain and
re-gate if misclassified. Never infer labels for walk-ins.

**G7 — Consent and data handling** · all
QR card states what is captured; header-only; walk-in data deleted after the demo; VM wiped of
captures before shutdown. Written policy in the runbook.

Critical path — infra: I1 → I2 → I3 → G2; I4 now; I5 before G5.
Critical path — code: C4 ∥ D2 now → D3 → D4 → D7; C0–C3 alongside; G2 (after I3 + C1) → G3/G4 → G5.

## 3. Shared interface (fixed so both sides proceed independently)

| Item | Value |
|---|---|
| VM | `agent-qos-p4dev`, `asia-south2-a`, external IP `34.131.42.32` (to be made static) |
| Repo on VM | `~/cn_project`; `.venv` (py3.11: pytest, ruff, ptf, mininet, sklearn) and `.venv-ryu` (py3.9) |
| WireGuard | `wg0`, server `10.42.0.1/24`, UDP `51820`, clients from `10.42.0.10`, full tunnel, DNS `10.42.0.1` |
| Netns `lan` | `wg0`; `gw-lan` `10.43.0.2/30`, default via `10.43.0.1` |
| Root netns | `gw-wan` `10.43.0.1/30`; route `10.42.0.0/24 via 10.43.0.2`; MASQUERADE out `ens4` |
| BMv2 attach | `gw-lan-sw` = LAN port, `gw-wan-sw` = WAN port, `gw-cpu` = CPU port 255; pipeline JSON from `build/` |
| Port numbers | proposed LAN = 1, WAN = 4 pending C1 |
| Dashboard | VM `127.0.0.1:8080`; Mac via SSH tunnel |
| Live snapshot | `results/live/snapshot.json`, atomic replace, schema = `eval.metrics` plot summary |

## 4. Evaluation plan for the paper

### 4.1 Claims under test

- **H1 (protection):** under a shared bottleneck with ≥ 50 % agent traffic, our pipeline keeps
  HUMAN_INTERACTIVE p99 one-way latency within a stated bound while FIFO's grows; target ≥ 30 %
  p99 reduction versus the best of the four baselines (requirements anchor).
- **H2 (overhead):** `agent_aware.p4` adds < 5 % p99 latency over `l2fwd` at the evaluated load
  (requirements anchor). *Currently falsified at 2.9 kpps on BMv2 — see §0; C4 decides whether
  this is a logging/punt artefact or an intrinsic software-switch limit.*
- **H3 (early classification):** per-class precision/recall at the 6/16/64-packet checkpoints on
  flows with independent orchestration labels (§7.20), reported per framework; UNKNOWN counted as
  a miss.
- **H4 (starvation-free):** AGENT_BULK is metered, never starved: tool-call completion fraction
  and completed-call percentiles reported alongside human latency.

### 4.2 Settings

1. **Controlled (primary evidence):** Mininet `choke_v1` on the VM, 10 Mbps bottleneck, systems
   {ours, fifo, diffserv, fairq, app_limiter}, agent share × burst grid, ≥ 5 seeds, 95 % t-CIs over
   seeds (`eval.metrics.aggregate_runs`). Traffic from the D2 profile: human video call, MCP-tool
   agent, agent bulk storm. Classifier tree fixed before the grid; equivalence gate 100 %.
2. **Live gateway (demonstration, secondary evidence):** real devices via WireGuard through the
   same pipeline on the same VM; FIFO vs ours on the same hardware; labelled team devices only
   for accuracy; walk-ins show predicted class only.

### 4.3 Measurements and procedure

Two-tap one-way latency and per-class throughput via `eval.pcap_metrics` (packet identity:
normalised IPv4 datagram hash); tool-call completion from the MCP target's JSONL; classification
from egress DSCP at the checkpoint ordinals; BMv2 CPU and punt counters for overhead; all runs
through `eval.run_experiment` (locked, append-only, config-hashed, §7.19). Anchor assertions via
`eval.metrics.assert_anchor_targets`. Any retuning (tree, ECN, meters, presets) is config-driven,
logged, and reported as such (P4.7).

### 4.4 Reporting the current evidence honestly

- The 2026-10-31 run (seed 1, 30 % share, low burst) is a smoke, not a result: ours 601 ms vs
  FIFO 112 ms HUMAN p99, classification collapsed to HUMAN. It will be reported as the baseline
  state before C4/D2/D3, not omitted.
- Training corpus is synthetic plus a proxy dataset (Mendeley 5pmnkshffm) — no real agent
  frameworks captured yet (P3.4/P3.8). State this; browser-agent recall is weak
  (`docs/browser_agent_diagnosis.md`).
- BMv2 is a software switch; throughput and latency figures bound the prototype, not the method.
- Controller is a standalone Thrift/AF_PACKET daemon, not a Ryu app as design.md says; reconcile
  before any Ryu claim.
- Live gateway: hairpin RTT via Delhi, WireGuard on every device, DSCP valid only inside the VM,
  no accuracy claims for walk-ins.

DESIGN GAP: §10 leaves low/med/high burst presets unspecified; agree numbers before D2/D4 tuning
so the demo and the grid use the same mapping.
