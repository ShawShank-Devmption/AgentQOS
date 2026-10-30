# Review 1 Implementation Status

## Current implementation audit — 28 October 2026

The September checkpoint below is historical. The working tree now contains substantial
additional implementation, including untracked files; these are not evidence of an integrated
release. Current acceptance status against `docs/tasks.md`:

| Area | Present and host-tested | Acceptance work left |
|---|---|---|
| ML — P3.3/P3.6/P3.7 | Fixed-point reference, real-pcap extraction, grouped training, tree compiler and equivalence simulation | Real agent corpus; PTF bit-exactness; live replay precision/recall |
| Data plane — P2.1/P3.1/P3.9/P3.10/P3.13 | L2 skeleton, generated headers, base Ethernet/IPv4/TCP/UDP parser and parser-test harness | Bounded TLS parsing, feature registers/sketch, punts, production classifier wiring, QoS and complete `agent_aware.p4` |
| Controller — P3.2/P3.11/P3.14 | Policy startup, JA4, bounded punt parsing, reclassification/aging core and epoch scheduler | Override action ABI agreement; live punt receiver/writer and scheduled loops in the Ryu app; WBA verification; restart/chaos and protection tests |
| Harness — P1.5/P3.4/P3.5/P3.8 | Mininet launch/smoke code, MCP HTTP target/client, external-framework command boundary, human replay, capture labels | Linux M1 evidence, real four-framework captures, human traces/browsing and label audit; agreed burst presets |
| Evaluation/demo — P3.12/P3.15/P3.16/P4.* | Locked append-only execution boundary, configs, pcap metrics, CIs/anchor checks, plotting and dashboard server/UI | Actual four-baseline implementations and runtime adapters; integrated classification/QoS smoke/demo; measured grid, overhead, state scaling and evasion; full figure set |
| Paper/release — P5.* | Review presentations and implementation notes | Research paper with traceable results, reproduction README and demonstrated release |

Priority order: establish Linux M1 evidence and complete the P4 stages incrementally; collect
the real corpus and validate classifier quality in parallel; integrate controller overrides;
then run live classification/QoS smoke, baseline evaluation and demo. Host unit tests alone
do not complete M1–M6.

### Interactive agents classified as human

The saved synthetic model's held-out interactive-agent recall is 61.2%, 42.6%, 33.3% at
6/16/64 packets. The problem is concentrated in browser agents: 138/187 browser-agent
checkpoints predict HUMAN; MCP-tool checkpoints have 98/104 correct, six BULK and none HUMAN.
Human and agent browsers share the same Chromium handshake and `_browse` behavior; their
page-gap differences often do not appear in a flow's early checkpoints. This is an evidence
limitation, not an incorrect mapping of the frozen class IDs.

P3.3 correction implemented: synthetic fanout now reads each checkpoint's current epoch,
anchors epochs to the first captured packet and counts actual first packets rather than
scheduled starts. Previously the 6-packet estimate was reused at 16/64 packets. Three
regressions first failed, then passed. No frozen schema or label was changed.

Comparison artifacts are in `results/fanout_fix_20261028/` (ignored generated results).
Generation/training seed 1, 40 clients/profile; all non-fanout columns match the old corpus,
and the held-out flow identities match. 951 fanout rows changed. Retraining produces a
depth-8 candidate with 2,283/2,283 compiled-table simulation agreement. The old model/demo
artifacts remain intact; the candidate is not promoted as a browser-detection fix.

| Synthetic evaluation | Previous model | Corrected candidate |
|---|---:|---:|
| Held-out interactive-agent recall, 6/16/64 packets | 61.2% / 42.6% / 33.3% | 62.6% / 42.6% / 33.3% |
| Held-out browser-agent recall, all checkpoints | 26.2% | 25.1% |
| Held-out humans classified as agents, all checkpoints | 9.5% | 7.9% |
| Fresh generation seed 5: MCP-tool recall, all checkpoints | 85.4% | 96.5% |
| Fresh generation seed 5: browser-agent recall, all checkpoints | 19.9% | 18.7% |

The fresh-seed comparison runs both frozen models on the same corrected feature corpus;
it was not used to tune either model. These are sklearn predictions on synthetic checkpoints,
not live P4 performance. The existing split groups flows, not entire browser sessions, so
real model selection also needs session/framework-separated evaluation. Synthetic fanout
still models exact per-client counts rather than hash collisions. No real accuracy claim
is supported. Browser-agent misclassification remains unresolved: collect independently
labeled real browser-agent and human sessions under comparable conditions before changing
model selection or requesting any frozen-feature contract expansion.

### Fresh verification

`make host-check` passes: lint/format, generated-header check, **352 passed, 1 skipped** and
seeded smoke-config validation. Full host tests required loopback socket access outside the
sandbox; the first sandbox-only run's socket failures were environmental. PTF remains skipped;
no live Mininet/P4/QoS or headline performance verification was performed in this audit.
Two existing dashboard formatting issues were normalized to make the lint gate pass.

---

**Project:** Agent-Aware Networking  
**Review date:** 9 September 2026  
**Milestone:** First 25% foundation and prototype slice

## Verified evidence

Run the complete host evidence gate from the repository root:

```bash
make review-1-host
```

Current result:

- Ruff lint and formatting checks pass.
- 34 pytest tests pass.
- `eval/configs/review_1_smoke.yaml` validates as a seeded 30-second `choke_v1` run.
- `AGENTS.md` and `CLAUDE.md` are byte-identical.

The implemented artifacts are:

- an implementation of the frozen shared contracts for class treatments, P4/controller identifiers,
  feature order, CPU header, labels, compiled-tree output, and experiment configuration, awaiting
  the task P0.3 team sign-off;
- a minimal v1model L2 forwarding program containing the M1 forwarding table and readable policy
  version register;
- deterministic `choke_v1` host, port, and bottleneck metadata;
- a controller-only BMv2 CLI boundary with validated register reads and explicit error reporting;
- orchestration-derived pcap flow labeling in the frozen `labels.csv` schema; and
- strict experiment YAML loading plus a Review 1 smoke configuration.

## P4 compilation - verified 11 September 2026

`p4src/l2fwd.p4` compiles with `p4c-bm2-ss` in the `p4lang/p4c:latest` container:

```bash
docker run --rm -v "$PWD":/w -w /w p4lang/p4c:latest \
  p4c-bm2-ss --std p4-16 -o build/l2fwd.json p4src/l2fwd.p4
```

The first attempt failed: the empty checksum controls were named `VerifyChecksum` and
`ComputeChecksum`, shadowing the v1model package type names. They are now
`VerifyChecksumControl` and `ComputeChecksumControl`. The emitted `build/l2fwd.json` carries the
frozen identifiers `IngressPipeline.tbl_l2_forward` and `IngressPipeline.policy_version`.

The container is linux/amd64 under emulation, so the pinned toolchain hashes for
`docs/ENVIRONMENT.md` still require a native Linux install.

## Evidence that still needs the Linux runtime

The current macOS host does not contain BMv2, Mininet, tshark, tcpreplay, or iperf3. The following
commands remain external verification and must not be shown as passed:

```bash
make dev-env
python -m controller.app --thrift-port 9090
```

The final M1 evidence is one iperf3 flow across the BMv2 switch followed by a successful live read of
`policy_version[0]`.

## Slide 11 wording

“The first implementation slice now has a frozen and tested interface layer, a minimal P4 forwarding
program, the `choke_v1` topology definition, controller register-read handling, orchestration-based
pcap labeling, and seeded experiment configuration. All 34 host-side tests and style checks pass,
and `l2fwd.p4` compiles under `p4c-bm2-ss` with the frozen table and register names preserved. Live
Mininet/iperf execution is awaiting the documented Linux toolchain; no classification or performance
result is claimed yet.”

## Design decision needed next

Bit-exact feature preprocessing cannot proceed safely until the team freezes the shift-division
rules, first-eight packet-size encoding, count-min sketch hashes/seeds, and ALPN numeric encoding.
These are listed in `docs/feature_arithmetic_design_gaps.md` for the next team sync and contract PR.
