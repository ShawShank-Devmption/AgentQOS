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
The macOS host lacks p4c, BMv2, Mininet, tshark, tcpreplay, and iperf3. Docker CLI is installed, but
the `p4lang/p4c:latest` image download did not complete on the available connection, so the P4
compile and live Mininet/iperf M1 check remain Linux/runtime verification items and must not be
presented as completed evidence.

DESIGN GAP: P2.4 cannot yet define bit-exact feature extraction because the shift-division rules,
first-eight size encoding, count-min sketch hashes/seeds, and ALPN numeric encoding are absent from
the frozen design. Details and the required contract process are recorded in
`docs/feature_arithmetic_design_gaps.md`.

## 2026-10-06 - Dev C traffic, evaluation, and dashboard implementation

Implemented the host-verifiable portions of tasks P1.5, P2.5, P2.6, P3.4, P3.5, P3.8, P3.12,
P3.15, P4.1, P4.2, P4.8, P5.3, and P5.5 without changing design section 4 contracts:

- executable Linux-only `choke_v1` launch/smoke lifecycle, deterministic L2 programming through
  `controller/switch_api.py`, and guaranteed cleanup;
- instrumented HTTP MCP target (`search`, `fetch`, `compute`) with request timing logs and a shared
  seeded agent-runner interface;
- four framework-labeled adapters, tcpreplay/browsing capture command plans, orchestration JSONL
  ingestion, frozen-schema labels, and corpus verification statistics;
- documented low/med/high burst presets and current MAWI/CAIDA access/usage constraints;
- four baseline command plans, per-baseline sanity configs, SHA-256 config identities, exclusive
  host locking, append-only run directories, and failure-preserving manifests;
- verified percentile, completion-time, per-class precision/recall, Student-t CI, and anchor math;
- deterministic storm-plan output, a read-only live dashboard, deterministic Fig. 2–6 generation,
  evaluation methodology, and two-rehearsal checklists.

Fresh host evidence:

- `make review-1-host`: Ruff clean; 89 pytest tests passed; Review 1 config validated.
- Dry-run expansion of Review 1, four baseline sanity configs, and `burst_sweep_v1`: 80 unique,
  append-only manifests (75 from the five-seed ours grid plus five sanity cells).
- `agent-qos-mcp-target:test` Docker image built and a live container returned all three tools from
  `tools/list`.
- Figure tests generated all six outputs twice with identical SHA-256 hashes and confirmed raw CSV
  inputs were unchanged.

External-runtime boundary: this host is Darwin and lacks p4c, p4c-bm2-ss, simple_switch, Mininet,
tshark, tcpreplay, and iperf3. Therefore M1 forwarding, persistent storm execution, real-framework
and human corpus collection, four baseline executions, the full grid, headline anchors, and two
demo rehearsals remain unverified and must not be reported as completed results.

DESIGN GAP: Browser Use, Playwright-agent, AutoGen, and Claude+MCP versions, container entrypoints,
and credential injection are not frozen, while repository policy prohibits adding their third-party
packages without team sign-off. The checked-in modules exercise the common MCP runner contract but
are not real-framework corpus evidence.

DESIGN GAP: the 30% p99-reduction and 5% overhead targets are specified in requirements/design but
are absent from `common/contracts.py`. `eval.metrics.evaluate_anchors` therefore requires both
thresholds as explicit inputs instead of redeclaring shared constants or modifying the frozen
contract without approvals.
