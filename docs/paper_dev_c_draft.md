# Dev C paper draft (P5.3)

This is claim-safe source text for later integration into the team's IEEE LaTeX tree. Bracketed
tokens are evidence placeholders and must not be replaced until the cited aggregate artifacts
exist. Literature keys refer to `docs/dev_c_literature_and_data.md`.

## Abstract draft

AI agents increasingly generate concurrent tool and browser traffic whose latency sensitivity is
not visible to conventional port-based policy. We present Agent-Aware Networking, a programmable
data-plane system that classifies flows as human-interactive, agent-interactive, agent-bulk, or
unknown and couples the inferred class to differentiated forwarding treatment. The design combines
bounded flow features in P4 with controller-assisted reclassification and a fail-open policy for
uncertain traffic. We evaluate it in a controlled BMv2/Mininet bottleneck against FIFO, static
port-based DiffServ, hashed per-flow fair queueing, and an application-layer limiter across agent
share, burst intensity, and at least five seeds. [RESULT: human p99 comparison, with CI and artifact
reference.] [RESULT: per-class precision/recall and checkpoint.] [RESULT: relative pipeline
overhead.] These results [CONCLUSION SUPPORTED BY RESULTS ONLY].

## Evaluation methodology

### Questions

The evaluation asks: (RQ1) whether the classifier distinguishes human-interactive,
agent-interactive, and agent-bulk flows early enough to act; (RQ2) whether class-aware treatment
protects human tail latency during an agent storm without starving interactive agents; (RQ3)
whether any benefit exceeds the four baselines across load levels; and (RQ4) what overhead,
state-scaling, and pacing-evasion tradeoffs the mechanism introduces.

### Testbed and controls

Experiments use the `choke_v1` Mininet topology with all senders traversing one BMv2 switch and a
configurable 10–50 Mbps bottleneck to the MCP target. The host is otherwise idle. CPU placement,
toolchain commits, and switch configuration remain fixed within each comparison. Full-pipeline
overhead is measured relative to minimal l2fwd on the same host and load, addressing the BMv2
artifact risk in design section 7.18.

Each immutable YAML configuration expands to system, agent-share, burst, and seed coordinates. A
SHA-256 config identity names the append-only output root, and one exclusive host lock prevents
concurrent experiments (section 7.19). Successful cells retain their run summary, telemetry,
labels, target timings, config identity, coordinate, and manifest attestation. Failed cells remain
available for diagnosis and are never silently reused.

Per-packet transit latency is derived from two line-buffered tshark taps that share the emulation
host clock: class-host-facing switch ports before forwarding/queueing and the target interface after
the bottleneck. IPv4/TCP addresses, identifiers, ports, sequence state, flags, payload length, and
frame length form a stable packet key. Matches are consumed one-to-one; forward and reverse delays
use the appropriate timestamp order, and unmatched packets are excluded. Completed artifacts must
declare `latency_method=matched_two_tap` and retain both telemetry files.

DESIGN GAP: section 10 says “embedded timestamps.” The implementation uses capture timestamps and
packet-identity matching so framework and iperf payloads remain unmodified. The paper must describe
the actual method and must not claim embedded-payload timestamps. A Linux sanity run must still
validate match coverage and clock behavior before this becomes experimental evidence.

### Workloads and ground truth

The agent workload comprises Browser Use, a Playwright-driven agent, AutoGen, and Claude+MCP
executing deterministic `search`, `fetch`, and `compute` tasks against the instrumented target.
Framework containers, dependency locks, image digests, task scripts, and seeds accompany any real
framework claim. The repository adapters alone demonstrate the common MCP wire contract.

Human workloads combine timing-faithful (`--multiplier=1.0`) MAWI/authorized-CAIDA replay,
consented browsing, and a paced 2 Mbps, 1200-byte TCP stream for the centerpiece scenario. MAWI
limitations include anonymization, asymmetric visibility, finite timestamp precision, and possible
capture loss. CAIDA data is used only by authorized users under the applicable agreement. iperf
background traffic is excluded from classification precision/recall.

Ground truth comes only from runner-controlled source identities and orchestration time windows;
no classified feature is reused as a label (section 7.20). A corpus enters training/evaluation only
when at least 95% of label rows trace to orchestration metadata. The corpus manifest records source,
terms, acquisition, preprocessing, hashes, consent where applicable, and retention policy.

### Compared systems

- FIFO/best effort: minimal l2fwd with one bottleneck queue.
- Static DiffServ: application-port DSCP marking with the same available queues; this tests whether
  fixed header rules suffice without behavior inference (RFC 2474/2475).
- Hashed per-flow fair queueing: the repository's round-robin approximation, explicitly not an
  implementation-equivalence claim for the Demers–Keshav–Shenker algorithm or FQ-CoDel.
- Application limiter: request limiting before the target behind the FIFO switch. HTTP rejections
  are measured as workload outcomes rather than converted into harness crashes.
- Proposed system: data-plane class inference plus the frozen class-to-DSCP/queue/meter policy and
  controller reclassification.

All five systems receive the identical agent-share × burst grid and at least five explicit seeds.
The low/medium/high preset definitions remain fixed in `harness/README.md`.

### Metrics and statistics

Primary protection metrics are human p50/p95/p99 matched two-tap packet transit time and throughput.
Tool-call completion is paired from target start/end nanoseconds; completed and rejected attempts
are both retained.
Classification reports one-vs-rest precision and recall per class at 6, 16, and 64 packets, with
undefined denominators left undefined. Scaling reports collision rate, accuracy, and state bytes.
The evasion plot pairs accuracy loss with agent completion slowdown under sampled human-like think
times.

Across seeds, the paper reports arithmetic means and two-sided 95% Student-t confidence intervals.
The centerpiece timeline uses the largest agent share shared by all five systems under the high
burst preset and a balanced seed set. The predeclared anchors are at least 30% human-p99 reduction
against the best baseline and less than 5% relative per-packet overhead. Both pass and fail outcomes
are reported. Every plotted number must resolve through an aggregate audit row to a successful,
hash-attested run manifest.

## Results section template

### Classification

At checkpoint [6/16/64], the classifier achieved [P/R WITH 95% CI] for human-interactive,
[P/R WITH 95% CI] for agent-interactive, and [P/R WITH 95% CI] for agent-bulk traffic (Fig. 3,
aggregate [PATH/HASH]). [Discuss confusion and UNKNOWN behavior without converting undefined values
to zero.] These values include [FRAMEWORK/CORPUS BREAKDOWN] and exclude unlabeled background.

### Human protection and agent completion

Under the balanced high-burst centerpiece at [AGENT SHARE]%, human p99 matched packet transit time
was [OURS] ms ([CI]) with the proposed system versus [FIFO], [DIFFSERV], [FAIRQ], and [LIMITER] ms
(Fig. 2). This corresponds to [REDUCTION]% against the best baseline and therefore [PASSES/FAILS]
the predeclared 30% anchor. Interactive-agent median/p99 completion changed by [VALUES], and request
rejection rates were [VALUES], exposing whether protection merely displaced harm to the agent
workload.

### Cost, scaling, and evasion

Relative to minimal l2fwd, the full pipeline changed per-packet latency by [VALUE]% ([CI]) on the
pinned host and [PASSES/FAILS] the 5% overhead anchor (Fig. 4). From 1k to 100k concurrent flows,
collision rate changed from [VALUE] to [VALUE], accuracy from [VALUE] to [VALUE], and allocated
state was [VALUE] bytes (Fig. 5). Human-like pacing changed classification accuracy by [VALUE]
points while increasing agent completion time or reducing throughput by [VALUE] (Fig. 6).

### Honest gap-analysis branch

If either anchor fails, report the observed effect, confidence interval, affected grid cells, and
most likely mechanism. Any follow-up tuning is a new config-hashed run across the complete seed set;
never replace or omit the failed evidence.

## Threats and limitations

BMv2 timing is host-sensitive, so results establish controlled relative behavior rather than
hardware-switch performance. MAWI and CAIDA traces are anonymized observations with asymmetric
paths and may not represent current enterprise traffic. Real-framework versions and task mixes can
shift rapidly. Encrypted traffic reduces direct semantics, making the early classifier dependent on
bounded timing/size/handshake features. Hash collisions and asymmetric visibility can degrade flow
state; uncertain or malformed traffic therefore follows the documented fail-open path. DSCP has
only domain-local force unless adjacent domains honor the same policy. Two-tap matching covers only
IPv4/TCP packets with stable identity fields, excludes unmatched packets, and still needs live
match-coverage validation. Finally, the Stream C literature moves quickly; the novelty statement is
bounded to the verified sources and requires a pre-submission rescan.

## Conclusion draft

This work asks whether a programmable network can recognize agent-generated traffic early enough
to protect interactive users during bursty tool activity. The implementation joins bounded P4
features, controller-assisted correction, and class-aware forwarding under fail-open invariants,
with an audited harness that treats configurations, seeds, failures, and artifacts as first-class
evidence. [RESULT-SUPPORTED SENTENCE ABOUT HUMAN TAIL LATENCY.] [RESULT-SUPPORTED SENTENCE ABOUT
CLASSIFICATION AND COST.] Regardless of whether the predeclared anchors pass, the evaluation makes
the tradeoff explicit: [SUPPORTED TRADEOFF]. Future work should validate the policy on hardware,
broaden consented and authorized corpora, and study cooperation between endpoint declarations and
behavioral inference without trusting either signal alone.

## Evidence gate before LaTeX integration

- Replace every bracketed token from audited aggregate files only.
- Add config path, config SHA-256, run-manifest hashes, seed set, and figure-input hash to the claim
  ledger.
- Confirm all five systems have identical coordinates and at least five successful seeds.
- Require `matched_two_tap` summaries, both telemetry files, and recorded match coverage.
- Include framework image digests and corpus manifests for corpus-dependent claims.
- Re-run the related-work search and check citation versions/venue status.
- Run two complete live rehearsals and archive their manifests before any demo-reliability claim.
