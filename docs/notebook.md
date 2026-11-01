# Lab Notebook

## 2026-09-09 - Review 1 baseline and first implementation slice

Read `AGENTS.md`, `requirements.md`, `design.md`, `tasks.md`, the project-details PDF/HTML, and all
11 pages of the supplied Course Project Review 1 template before planning or editing source code.
The review template requires the title, objectives, gap, proposed system, architecture, tools,
modules, module input/process/output, algorithm, and visible evidence of roughly 25% progress.

The selected Review 1 slice is the shared contract foundation plus a narrow vertical prototype:

- P0.2/P0.3/P0.5/P0.6: repository scaffold, frozen Python contracts, instruction-file equality,
  lint, and tests;
- P1.5: minimal L2 P4 source, deterministic `choke_v1` topology, and a BMv2 policy-version read;
- P2.6: strict validation of the frozen experiment YAML shape and a 30-second smoke config; and
- initial P3.4/P3.5 data work: orchestration windows joined to pcap 5-tuples to produce the frozen
  `labels.csv` schema without feature-derived labels (design section 7.20).

Host verification after these changes: Ruff and 34 pytest tests pass. The experiment smoke config
validates.
The macOS host lacks p4c, BMv2, Mininet, tshark, tcpreplay, and iperf3 natively, so the live
Mininet/iperf M1 check remains a Linux/runtime verification item and must not be presented as
completed evidence.

## 2026-09-11 - P4 compile verified; `l2fwd.p4` control-name collision fixed

The `p4lang/p4c:latest` image finished downloading, so the P4 compile was attempted for the first
time. It failed:

```
p4c-bm2-ss --std p4-16 -o build/l2fwd.json p4src/l2fwd.p4
  p4src/l2fwd.p4(30): [--Werror=duplicate] error: VerifyChecksum duplicates VerifyChecksum.
  p4src/l2fwd.p4(81): [--Werror=duplicate] error: ComputeChecksum duplicates ComputeChecksum.
```

The two empty checksum control blocks were named `VerifyChecksum` and `ComputeChecksum`, which
shadow the v1model package type names declared in `v1include/v1model.p4`. Renamed them to
`VerifyChecksumControl` and `ComputeChecksumControl` (matching the file's own `IngressPipeline` /
`PacketDeparser` convention) and updated the `V1Switch` instantiation.

The program now compiles. `build/l2fwd.json` preserves the frozen design section 4.2 identifiers:
table `IngressPipeline.tbl_l2_forward` and register `IngressPipeline.policy_version`. The remaining
`'policy_version' is unused` warning is expected at M1 — the register is read over the BMv2 CLI by
`controller/switch_api.py`, not from the pipeline.

Note the image is linux/amd64 emulated on arm64; the compile is valid but the pinned-hash record in
`docs/ENVIRONMENT.md` still needs a native Linux run. Live Mininet/BMv2 and iperf3 remain pending.

DESIGN GAP: P2.4 cannot yet define bit-exact feature extraction because the shift-division rules,
first-eight size encoding, count-min sketch hashes/seeds, and ALPN numeric encoding are absent from
the frozen design. Details and the required contract process are recorded in
`docs/feature_arithmetic_design_gaps.md`.

## 2026-10-07 - P2.4 feature arithmetic proposed (Dev B)

`docs/feature_arithmetic.md` now gives exact fixed-point definitions for all ten features. It
covers the shift division, the ×100 expansion, the first-8 bitmask (MSB = first packet, ≥128 B),
crc32+salt hashing, the ALPN enum, ClientHello bounds, and IAT seeding. The Python reference is
`common/feature_math.py` plus `ml/extract_features.py`. Worked examples at 6/16/64 packets are in
`tests/fixtures/feature_worked_examples.json`. Contract additions are in `common/contracts.py`,
including the new `reg_first_ts` register, which is a frozen-contract change awaiting 2 approvals.

**Pending:** Dev A agreement and a PTF replay of the fixture through `features.p4`. P2.4 is not
done until that replay matches exactly.

## 2026-10-07 - P3.6/P3.7 tree pipeline on synthetic data (Dev B)

`ml/train.py` trains a depth-8, class-weighted tree. CV and the 20% held-out split are both
grouped by flow, so a flow's checkpoint rows never straddle a split, and the §7.14 path-coverage
check is applied. `ml/compile_tree.py` turns the tree into §4.5 entries plus the generated
`tbl_tree_l*` P4 declarations. Those declarations compile under p4c in a scratch v1model harness:
8 range tables, 11 keys each, sizes 2–256. `common/compiled_tree.classify` simulates the BMv2
tables and is shared with the 3.9 controller. `ml/verify_equivalence.py` gives 100% agreement on
held-out rows, 10k random vectors (including field max and values above 2^24) and split-boundary
probes, and it detects a tampered boundary.

**Finding (§7.9):** sklearn compares `float32(x) <= threshold`, so above 2^24 the switch boundary
is not `floor(threshold)`. The compiler emits the largest k with `float32(k) <= threshold`
(for example 16777217, where floor gives 16777216).

DESIGN GAP G19: the generated key paths `meta.tree_node` and `meta.features.<name>` must match
Dev A's metadata struct. Running on the real corpus waits for P3.3 and Dev C's P3.8.

## 2026-10-28 — P3.3 synthetic fanout correction and implementation audit

Traced interactive-agent false-human predictions to browser-agent flows (138/187 held-out
checkpoints), while MCP-tool traffic had no HUMAN predictions. The synthetic browser profiles
share TLS and per-page behavior. Separately reproduced stale fanout values across checkpoints,
absolute rather than capture-relative epochs, and claims based on scheduled rather than actual
packet arrival. Three new tests failed before the fix in `harness/synthetic_traffic.py`.

Replayed each flow through the shared `update_flow`/`feature_vector` implementation and read
current-epoch fanout at each 6/16/64-packet checkpoint. No frozen contract changed. New artifacts
are append-only under `results/fanout_fix_20261028/`; previous synthetic/demo models remain
unchanged. Generation/training seed 1, 40 clients/profile, same held-out flow identities;
951 changed feature rows, all other columns identical. Candidate depth 8; compiled equivalence
2,283/2,283. `comparison.json` preserves full confusion matrices and a fresh generation-seed-5
comparison without tuning on that corpus. MCP recall improves; browser recall does not.

Fresh `make host-check` with loopback access passes: 352 tests passed, one PTF module skipped,
lint/format and generated-header checks pass, smoke YAML validates. Two pre-existing dashboard
formatting failures were normalized. No P4 or live Mininet verification is claimed here.
See `docs/review_1_status.md` for current acceptance gaps and the unresolved browser problem.

## 2026-10-28 — P3.10/P3.13/P3.16 integration checkpoint

The generated tree tables, per-flow features, two-epoch four-row sketch, bounded ClientHello
extension/ALPN parse, exact override, class action, meters, egress ECN hysteresis, and CPU clone
path now compile together with Docker `p4lang/p4c:latest`. The DiffServ baseline compiles as a
separate static-port P4 program. All controller-visible BMv2 objects use absolute P4 names so the
compiled artifact exposes the frozen unqualified CLI names.

An isolated BMv2 container packet check exposed a runtime requirement: `simple_switch` defaults
to one priority queue, whereas the frozen treatments use priorities 0, 1, and 2. The Mininet
launcher now derives and passes `--priority-queues 3`. With that setting, a 156-byte ClientHello
was forwarded byte-for-byte, cloned to CPU with the frozen 8-byte header, and produced
`reg_proto_meta=33794` (payload-seen, four extensions, H2). The four sketch rows read 1 and an
exact HUMAN override changed the next packet's DSCP to 34. This was a container BMv2 check, not
the Linux Mininet bottleneck run or a PTF suite result. `tests/ptf/test_agent_pipeline.py` records
the packet-level assertions for the Linux environment.

The host gate after integration: 474 pytest tests passed, four skipped; `make lint` passed; all
three top-level P4 programs compiled. The tree fixture under `tests/fixtures/smoke_tree.json`
always returns UNKNOWN and is explicitly for plumbing only. The existing
`results/fanout_fix_20261028/compiled_tree.json` is a trained synthetic-profile candidate (depth
8, 64 entries, held-out report in the same directory). No real labeled corpus, Mininet queue
measurement, four-baseline result, or paper headline result has been established.

DESIGN GAP: `tbl_diffserv_port` is a baseline-only table absent from frozen §4.2; team review is
needed before merge. The current implemented controller is a standalone Thrift/AF_PACKET daemon,
while the design describes Ryu. Its implementation and the paper must be reconciled before a Ryu
claim. SNI length bucket and cipher count are not exposed in the frozen classifier vector; the
parser currently records extension count and ALPN for the classifier.

## 2026-10-31 — First Linux evidence on the GCP VM (`agent-qos-p4dev`, asia-south2-a)

Repo synced at `47c6023`. Gates on the VM: `make lint` green; `make test` 484 passed with the
three PTF modules wrongly collected by plain pytest; `make check-py39` 153 passed; `make build`
compiled `l2fwd`, `agent_aware`, `diffserv` with real `p4c-bm2-ss`. PTF on real BMv2:
`test_diffserv` OK; `test_parser` aborted by a harness error (AF_PACKET `EINVAL` on the <14-byte
`short_ethernet` frame); `test_agent_pipeline` failed `reg_cm_sketch_0 == 1` (read 2 after one
ClientHello). M1 Mininet smoke passed at 18.8 Mbps on the 20 Mbps `choke_v1` link.

`review_1_smoke` (seed 1, 30 % share, low burst, 20 Mbps, 30 s) completed with success for ours,
fifo and diffserv — the first measured runs. HUMAN p99: ours 601 ms, fifo 112 ms, diffserv 111 ms;
latency identical across classes within each system, so the ~490 ms is pipeline processing
overhead, not queue differentiation. Live classification under ours collapsed to HUMAN
(precision 0.014; AGENT_INTERACTIVE recall 0; AGENT_BULK recall 0.38–0.45): the UDP stress
workload is not a trained shape. Neither anchor holds in this configuration. Follow-up tasks
C0–C4 and D2–D3 in `docs/demo_tasks.md`. Evidence: `results/linux-live/`, `results/linux-ptf/`
on the VM (append-only; copy off per I6).

## 2026-10-31 — VM resize, access hardening, live gateway (I1–I4, I6)

VM `agent-qos-p4dev` resized `e2-standard-16` → `e2-standard-4` after promoting `34.131.42.32` to
static address `aan-gateway-ip` (a stopped VM releases an ephemeral IP). SSH restricted to Google
IAP (`default-allow-ssh` → `35.235.240.0/20`); TCP 22 confirmed closed from the internet; access via
`harness/gateway/vm.sh` (IAP). WireGuard rule `aan-allow-wireguard` (UDP 51820, tag `aan-gateway`).
Budget alert ₹3,000/month on the project. `default-allow-rdp` (TCP 3389 from anywhere) remains.

Live gateway installed from `harness/gateway/`: `wg0` in netns `aan-lan` with its UDP socket in the
root namespace, client traffic forced across the switch-facing veths `gw-lan-sw`/`gw-wan-sw`
(bridged in bypass mode until G2 attaches BMv2), MASQUERADE to `ens4`, MSS clamping, MTU 1280,
boot-time unit. Verified on the VM: selftest PASS (egress IP = VM IP, packets counted on
`gw-lan-sw`, RTT to 1.1.1.1 ≈ 1.2 ms); two restarts safe; bypass off → no connectivity, on → PASS;
teardown idempotent with no leftovers; reboot → gateway active and PASS. Phone test (same day): Android
over cellular reached the internet as `34.131.42.32`; handshake from a mobile-carrier endpoint,
≈ 500 KB each way, matched by `gw-lan-sw` counters.

C4 lead: `harness/runtime.py` starts `simple_switch` without `--log-console`, but the installed
BMv2 1.15.7 has logging compiled in (logging options accepted). BMv2 performance builds use
`--disable-logging-macros --disable-elogger` and `-O3`; first comparison for the 490 ms overhead.

DESIGN GAP: `harness/gateway/` is a new subdirectory not in the frozen design.md §3 layout
(handoff §6 item 7). Gateway names/addresses live in `harness/gateway/gateway.env`; C1 decides
whether the switch-facing names move into `common/contracts.py`.

Evidence of the first Linux run archived to `results/vm-evidence/2026-10-31/evidence.tar.gz`
(SHA-256 `f7bfe5f6…1967165`).

## 2026-10-31 — Labelled corpus sessions through the gateway (P3.4/P3.8 → G6)

`harness/capture.py` now runs capture sessions (`python -m harness.capture start|stop`, wrapped by
`vm.sh capture-start|capture-stop`): full-packet tcpdump of one device's tunnel address on
`gw-lan-sw`, orchestration window = [listening, stop], labels via the existing
`label_pcap_flows` in the frozen §4.4 schema. Safeguards: new session directory only (§7.19);
kernel/interface drops fail the session without labels; `validate_capture` before labelling;
TCP flows whose first captured packet is not the device's SYN are excluded and counted, so
checkpoint features match what the switch would compute for a new flow. Under sudo, tcpdump drops
to the invoking user, who owns the session directory. Host tests use a fake tcpdump; the first real
session on the VM is the runtime check. Labels are per device and window: other traffic from the
device during a session inherits the label (operator keeps the device otherwise idle).

## 2026-10-31 — C4/P4.3 overhead measurement preparation (Dev A)

Scope: design §10 / §7.18 (relative overhead on the same host, switch pinned to one CPU),
§7.19 (locked, append-only runs), and §7.6 (punt cap). No frozen contracts changed.

Source inspection: `harness/runtime.py:switch_command` does **not** pass `--log-console`.
This does not establish whether the VM's binary was compiled with logging/debug support.
No logging/punt fix is justified by this inspection alone. The earlier 601 ms versus 112 ms
HUMAN p99 smoke remains the only recorded live evidence; these are not new measurements.

Implemented `eval.overhead` as a workload for the existing `eval.run_experiment` runner,
using its exclusive lock, config snapshots, command archival, and append-only result paths.
It reuses the existing seeded UDP scheduler, profiles, network adapters, controller, and metrics.
Only the aggregate offered packet rate changes; the original profile is archived unchanged and
the requested pps is explicit in the archived command and `overhead.json`.

`RuntimeConfig.switch_cpu` launches BMv2 through `taskset` before threads are created.
Unavailable CPUs/taskset fail before allocating the network. The workload verifies affinity
and records process-wide CPU time, CPU-port TX packets (emitted clones, not punt requests),
configured punt rate/burst, launch argv, binary SHA-256, switch log bytes, and compiled table
capacity/logical register bits. CPU-port measurements for FIFO are unavailable, not zero.
The punt-cap check includes the frozen burst allowance; it is an average-window diagnostic,
not a proof of short-window enforcement. No new P4 counters or ABI additions are introduced.

Measurement limits: one-way latency uses the existing endpoint send/receive timestamps, so
host scheduling is included. CPU/counter sampling surrounds actor startup and drain as well
as traffic; `sample_elapsed_s` records that denominator. These are prototype measurements,
not isolated hardware pipeline latency. `summary.json` reports losses alongside latency;
do not interpret surviving-packet percentiles without checking loss and achieved offered rate
from sender logs. The coarse sweep below does not prove a global maximum safe packet rate.

### VM execution still required

`gcloud` is not installed on this Mac session. No Linux network or live measurement has been
run for this change. Use the team VM procedure in `docs/handoff_2026-10-31.md` §2 after syncing
these working-tree changes (its `git archive HEAD` example only includes committed files).
Do not overwrite existing VM evidence. Use the same available CPU for all six runs, with the
same built binaries and compiled model; keep other workloads idle.

From the VM repository root, after `make lint && make test && make build`:

```sh
# Set TASK_SWITCH_CPU to an allowed CPU shown by taskset -pc $$.
# Set AAN_COMPILED_TREE to the existing trained/equivalence-gated compiled_tree.json.
export TASK_SWITCH_CPU=0
export AAN_COMPILED_TREE=/path/to/equivalence-gated/compiled_tree.json
for pps in 100 1000 2900; do
  for system in fifo ours; do
    sudo env PATH="$PATH" PYTHONPATH="$PWD" AAN_COMPILED_TREE="$AAN_COMPILED_TREE" \
      .venv/bin/python -m eval.run_experiment "eval/configs/overhead_${pps}_${system}.yaml" \
      --execute .venv/bin/python -m eval.overhead --pps "$pps" --switch-cpu "$TASK_SWITCH_CPU"
  done
done
```

The YAML schema remains exactly design §4.6. Each rate/system pair has a distinct config/output
root. A repeat requires a new config name/output root; never delete or reuse old evidence.
The same profile, seed, class shares, duration, and packet schedule are used within each pair.
If any level fails, retain the failure artifacts before investigating/repeating.

Record the actual output paths/config hashes and fill this table from `summary.json` and
`overhead.json`. Report HUMAN p50/p99 plus other classes, and compute aggregate percentiles
from observations if an all-packet table is needed; never average class percentiles.

| Requested pps | Pipeline | HUMAN p50 ms | HUMAN p99 ms | Switch CPU % | CPU-port TX / sample s |
|---|---|---|---|---|---|
| 100 | l2fwd | unavailable | unavailable | unavailable | unavailable (no CPU port) |
| 100 | agent_aware | unavailable | unavailable | unavailable | unavailable |
| 1000 | l2fwd | unavailable | unavailable | unavailable | unavailable (no CPU port) |
| 1000 | agent_aware | unavailable | unavailable | unavailable | unavailable |
| 2900 | l2fwd | unavailable | unavailable | unavailable | unavailable (no CPU port) |
| 2900 | agent_aware | unavailable | unavailable | unavailable | unavailable |

Then evaluate `(ours_p99 / fifo_p99) - 1 < 0.05` for matched measurements, inspect actual
sender cadence, losses, CPU saturation, log volume and punt cap. Investigate the installed
BMv2 build flags/version separately before attributing cost to logging. Refine the pps grid
around any boundary with new configs; do not change demo link rates until measured evidence
supports a safe operating point. The permitted link range remains 10–50 Mbps (§4.6).
If no permitted rate meets the anchor, report that result rather than bypassing the contract.

Re-run `review_1_smoke` with `eval.workload` under both `ours` and `fifo` using new output roots,
same seed/profile/model and hardware conditions, and report
`HUMAN_INTERACTIVE.latency_ms.p99(ours) / HUMAN_INTERACTIVE.latency_ms.p99(fifo)`.
The ratio, maximum passing pps, cause of the slowdown, and overhead table remain **pending VM
measurement**. C4 is not complete and D4 remains gated.

Host verification for this preparation:

- `make lint`: passed (`ruff check` and `ruff format --check`, 98 Python files).
- `.venv/bin/python -m pytest tests/test_overhead.py tests/test_runtime.py`: 25 passed.
- All six `overhead_*.yaml` configs passed `eval.run_experiment.load_config`.
- `make test`: 493 passed, 4 skipped (16.38 s), after allowing loopback sockets outside
  the filesystem/network sandbox. The sandboxed attempt failed existing HTTP/UDP tests with
  `PermissionError` on socket bind; it did not expose an overhead-code failure.
- `git diff --check`: passed.
- Baseline ML/plot packages were installed into the existing local `.venv` to enable full
  collection; no project dependencies or manifests were added.
- Real p4c/BMv2/Mininet/VM checks: not run here; required before C4 can be marked complete.

## 2026-10-31 — remaining Dev A implementation

C2 (design §5.1/§7.15): replaced the unsendable 13-byte test frame with an Ethernet-only IPv4
frame; all 24 cases are host-inspectable and remain PTF forwarding assertions.

C3 (design §5.2/§7.10/§7.12): the failing assertion followed **two** distinct source flows,
not just the ClientHello. Added source-sketch checks 0→1→2 around those sends and an unchanged
counter check after the fragment. No feature/sketch P4 logic changed. This fixes the observable
test-ordering error; BMv2 confirmation is still required.

C1: the project owner answered yes when asked whether the team approved
`docs/c1_live_gateway_proposal.md`. Implemented that contract extension in design §4 and
common/contracts.py, retaining choke_v1 as the original topology. Gateway artifacts use separate
LAN/WAN flags and reflected-flood exclusion. Generated constants remain in sync. The P4 change
is a small conditional build-mode increment, not a replacement pipeline.

G2: added inline deployment with explicit FIFO/ours artifacts, peer MAC validation, separate CPU
interface, bidirectional queue setup, aggregate-shaping prerequisites, exclusive host ownership,
Thrift-port collision protection, health monitoring and switch-stop-before-bypass cleanup.
DESIGN GAP: I3 scripts/interfaces and the required `netsetup.sh --switch` handover are absent.
The launcher deliberately refuses takeover until these prerequisites exist. Host tests cover
normal completion, handover/start/policy/workload failures, dead switch, missing prerequisites,
invalid MACs and original artifact rejection. Recovery <5 s and actual shaping are unmeasured.

D4 (design §5.5/§7.7/§7.16): added PTF real-queue set/clear and separate controlled tests for
protective index selection, zero interactive red drops, bulk non-ECT red drop and ECN behavior.
Added pre-run latency-target declaration and paired measured-report checks; PTF-derived counts
are explicitly isolated test evidence, never live workload counters. Production preset values
are unchanged. D2/D3/C4 readiness and the actual demo comparison remain pending.

C4: added immutable paired reports from completed experiments, including aggregate percentiles,
HUMAN ratio, loss/achieved rate, punt diagnostics and artifact footprint. Invalid or mismatched
inputs and incomplete sender schedules are rejected. No new latency or CPU results were made.

`make build` was attempted and stopped at `p4c-bm2-ss: No such file or directory`.
Per AGENTS.md's incremental compiler gate, further P4 work requires actual compiler output.
This host also has no gcloud installation/session; Docker daemon is unavailable. No VM has
been started, stopped or modified in this session. Full usage, per-task review units and
remaining acceptance checks are documented in `docs/dev_a_verification.md`.

Final host verification after these edits: `make lint` passed (108 Python files formatted),
`make check-generated` passed, `make test` **534 passed, 6 skipped in 9.44 s**. Five PTF
modules are skipped without the Linux PTF environment; the remaining skip is an existing
host-dependent test. All YAML experiment configs passed `load_config`; `git diff --check`
passed. These results do not replace the blocked Linux compiler/PTF/live acceptance checks.

## 2026-10-31 — P4.4 and P5.1 follow-up

P4.4 (design §10; §7.1/§7.4/§7.11/§7.19/§7.20): implemented deterministic distinct-tuple
stress trace preparation, original frozen CSV labels from seeded orchestration, a real
Linux two-tap workload adapter for eval.run_experiment, register snapshots through SwitchApi,
observed offered-concurrency/refresh checks, actual collision event/occupied-slot analysis,
switch RSS and logical register storage, and complete-seed Fig. 5 generation. Five new YAML
configs cover 1k→100k flows and five seeds; the workload profile is local JSON and does not
change the frozen experiment field set. Checkpoints not reached by the input stay null.

Ran the 25-cell analytical ownership plan on the host at
`results/state-scaling-plan-dev-a-20261031/plan.json`. Its provenance explicitly says it is
not a switch measurement, and actual collision/accuracy fields are null. No live scaling
sweep or measured Fig. 5 has been claimed. See `docs/state_scaling.md` for commands and limits.

P5.1: wrote insertable IEEE LaTeX design, implementation, scaling procedure and limitations
sections in `docs/paper_dataplane.tex`. Numbers are contract values or explicit targets;
there are no invented performance claims. The source identifies the current controller as
Thrift/AF_PACKET rather than asserting Ryu integration. Full manuscript compilation is not
verified because this host has no LaTeX compiler; the Word artifact was not modified.

DESIGN GAP / C3: further inspection found that §7.15 forbids writes for malformed TLS while
the earlier fixture expected a slot claim and payload-seen flag. The updated PTF fixture now
counts a second *valid* TCP flow separately, then requires malformed TLS to leave key,
packet count, protocol metadata and sketch unchanged. This preserves the frozen requirement.
The pipeline conformance fix and PTF result remain pending the required compiler/VM gate;
the earlier explanation of a test-ordering error alone was not complete acceptance.

Installed Google Cloud CLI 591.0.0 and verified it runs. No active account was configured;
opened `gcloud auth login --brief` for the user to complete in the browser. A local user
Google account must be authenticated/granted access; there is no separate assistant email
or Google account to invite. No VM has been modified in this follow-up.

Final host checks: `make lint` passed (113 files), `make check-generated` passed,
`make test` **552 passed, 6 skipped** (10.63 s after the final PTF fixture update).
All YAML configs validate and `git diff --check` passed. The skipped Linux PTF modules and
VM experiments remain acceptance gates; the host test count does not establish their result.

Authentication follow-up: the browser login completed successfully. A read-only describe
request for agent-qos-p4dev in vivid-rush-513007-k5 / asia-south2-a was rejected with missing
`compute.instances.get` permission. Cloud authentication is ready; project/VM authorization
is the current external blocker. No VM start/stop/sync/deployment was performed.

## 2026-11-01 — Integration: Dev A snapshot merged, Dev C D2 ported onto the live runtime

`origin/ananya` (C1, C2, C3, C4 tooling, D4 tests, G2, P4.4) merged into `origin/shashank`. Its
copies of `harness/gateway/{vm.sh,bypass.sh,netsetup.sh}` were an older version of the
VM-verified scripts and were dropped; G2 now calls `bypass.sh off` / `bypass.sh on`.

D2 (`origin/jobin` `7f7b43a`) had been back-ported to a branch without the live runtime, so its
`eval.workload` was a plan-only CLI. Ported here onto the existing runtime instead: profile
`traffic_shapes` extension, shaped planner, variable-size sender and join, plan-based queue rate,
plus `eval.workload --profile PATH` so a cell picks the profile on the `--execute` command line
(the frozen experiment-config schema is unchanged). The plan-only CLI and its tests were not
ported. Host: pytest and lint green. Not yet run on the VM: `make build` for the merged P4,
PTF suites, a D2 cell through BMv2.

## 2026-11-02 — Background noise removed from the corpus build (P3.6/P3.8)

Jobin's first Codex sessions showed ~20–26 % of labelled flows were Mac background services
(iCloud, Docker Desktop, WhatsApp, Google Calendar, NTP). `ml.build_corpus` now builds the
training set from the sessions without them (rules: `eval/configs/corpus_noise.json`); sessions are
not modified and dropped flows still feed slot ownership and fanout. Build
`results/corpus-build-2026-11-02` on the VM (17 complete sessions; `jobin-codex-agent-04` was still
recording): 103 flows dropped (WhatsApp chat 31, NTP 31, Apple 16, WhatsApp 11, Docker 6, iCloud 5,
Apple push 2); 2,648 kept. 12 min on the VM.

Residual check with tshark (diagnostic only): 81 kept flows (3.1 %) still go to background
services, all over QUIC (iCloud 37, Google Calendar 36, Apple 9, WhatsApp 1). Every TCP one was
caught. QUIC names need decrypting the Initial packet (RFC 9001 §5: AES-128-GCM and header
protection), which needs a crypto library, and Jobin's Mac sends DNS over HTTPS (1.1.1.1 UDP 443)
so DNS gives no names either.

DESIGN GAP: QUIC SNI for noise removal needs `cryptography` (not in the dependency baseline;
present on the VM only incidentally) or tshark — team sign-off before adding either.


Codex recordings now start with `--disable remote_plugin --disable plugins --disable apps`: every
Jobin Codex session began with a ~44–53 MB plugin-catalog download from chatgpt.com (the same host
as its model traffic, so the corpus filter cannot separate it). Flags verified with
`codex features list` (all three false; misspelt names are rejected) and a sandboxed crawl still
fetches pages; whether the download is gone is checked on the next captured session.
