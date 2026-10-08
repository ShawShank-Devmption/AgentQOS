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

Live per-class transit latency matches the same IPv4/TCP packet at the five class-host-facing switch
ports and at the target interface. Both tshark processes use the emulation host's clock. The match
key includes addresses, IPv4 ID, protocol, TCP ports, sequence/acknowledgment numbers, payload
length, flags, and frame length; matches are consumed one-to-one in timestamp order. Forward delay
is target timestamp minus source-side timestamp, and reverse delay uses the opposite order.
Unmatched packets are excluded rather than assigned an ACK RTT. Completed run summaries and
aggregates require `latency_method=matched_two_tap` plus both telemetry artifacts.

Throughput is the sum of both directions' target-tap frame bytes over each sliding window. Human and
agent latency use linearly interpolated p50/p95/p99 values. The paced 2 Mbps, 1200-byte TCP human
stream remains visible to the same classifier while approximating an interactive video workload
instead of saturating the link as an unconstrained bulk transfer. Tool completion time is paired
directly from the target's request start/end nanoseconds. Classification reports one-vs-rest
precision and recall for every training class, with undefined denominators reported as undefined
rather than zero. Across seeds, means and two-sided 95% Student-t confidence intervals are reported.

Every successful cell writes `run_summary.json`; the enclosing manifest records its SHA-256.
`eval.aggregate` verifies those hashes and cell coordinates, emits an audit trail and per-run rows,
and computes Student-t intervals only for multi-seed configs. The centerpiece selects the largest
agent share shared by all five systems under the high burst preset and averages aligned one-second
human-p99 samples across a balanced seed set.

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

The host unit/lint evidence verifies schemas, arithmetic, locking, manifests, two-tap identity
matching (including both directions), metric math, dashboard serving, and deterministic figure
generation. The persistent executor is implemented for Linux and the four l2fwd baselines. This
host evidence does not verify clock/capture behavior in Mininet, P4 compilation, forwarding,
real-framework captures, the full grid, headline anchors, or rehearsals. Those claims require the
documented Linux environment and archived raw evidence. The proposed-system run additionally
requires the absent Dev A/Dev B `agent_aware.p4`, policy install, and controller lifecycle. Dry-run
manifests remain planning artifacts only.
