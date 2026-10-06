# Evaluation methodology draft (Dev C / P5.3)

## Experimental control

All runs use the `choke_v1` topology and the same 10–50 Mbps configurable bottleneck. The Linux VM
must otherwise be idle, BMv2 and p4c commits must match `docs/ENVIRONMENT.md`, and CPU affinity must
be held constant across the full pipeline and minimal-l2fwd comparisons (design section 7.18).
Each run is defined by one immutable YAML config, its SHA-256 hash, a system variant, agent share,
burst preset, and explicit seed. One exclusive host lock prevents overlapping runs. A failed or
interrupted output directory is retained and never reused.

## Workloads and ground truth

Agent traffic comprises Browser Use, a Playwright-driven agent, AutoGen, and Claude+MCP hitting the
instrumented `search`, `fetch`, and `compute` target. Real-corpus claims require pinned framework
containers and archived image digests; the dependency-free adapters in `harness/agents/` alone are
not corpus evidence. Human traffic combines timing-faithful MAWI/CAIDA replay with at least two
hours of consented, anonymized browsing capture. Background iperf traffic is excluded from
classification precision/recall.

Labels are joined from runner-owned source IP and time windows, never inferred from classification
features (section 7.20). A corpus is accepted only after at least 95% of flow rows verify against
orchestration metadata. Trace identifiers, source terms, preprocessing, hashes, consent record,
and retention date accompany the corpus manifest.

## Systems and runs

We compare the proposed system with FIFO/best effort, static port-based DiffServ, per-flow fair
queueing, and an nginx application-layer limiter. Each system runs the same agent-share × burst
grid with at least five seeds. Burst presets are defined once in `harness/README.md`. No values are
tuned inside an individual run.

## Metrics and statistical treatment

Human and agent latency use linearly interpolated p50/p95/p99 values. Tool completion time is
paired directly from the target's request start/end nanoseconds. Classification reports one-vs-rest
precision and recall for every training class, with undefined denominators reported as undefined
rather than zero. Across seeds, means and two-sided 95% Student-t confidence intervals are reported.

The headline checks compare human p99 against the best baseline and relative per-packet overhead
against minimal l2fwd. The design targets (30% reduction and under 5% overhead) are supplied
explicitly to `eval.metrics.evaluate_anchors` until the team approves shared constants; failures
are printed as failures, not omitted. Interactive-agent completion times are reported alongside
human protection to expose starvation.

## Figures and traceability

`eval/plots.py` produces Fig. 2 (human p99 timeline), Fig. 3 (per-class precision/recall), Fig. 4
(pipeline latency CDF), Fig. 5 (accuracy/collision/memory scaling), Fig. 6 (evasion accuracy and
throughput penalty), and the feature-importance table. Each aggregate input must retain an audit
mapping to run manifests, config hashes, and seeds. Figure generation never edits raw results.

## Current evidence boundary

The host unit/lint evidence verifies schemas, arithmetic, locking, manifests, metric math,
dashboard serving, and deterministic figure generation. It does not verify P4 compilation,
Mininet forwarding, real-framework captures, the full grid, headline anchors, or rehearsals. Those
claims require the documented Linux environment and archived raw evidence. Live execution is
currently fail-closed before baseline setup because the required Person 1/2 agent-aware P4 and
controller pipeline is absent; dry-run manifests are planning artifacts only.
