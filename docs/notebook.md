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
