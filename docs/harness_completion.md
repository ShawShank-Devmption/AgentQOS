# Harness implementation evidence — 2026-10-28

Scope: P1.5/P2.5/P3.4/P3.5, design §9/§11. Existing `harness/topology.py`, `capture.py`,
`synthetic_traffic.py`, and `client_hellos.json` were preserved. Frozen design §3 paths are used:
`harness/mcp_target/`, `harness/agents/`, and `harness/human/`.

Contracts consumed: `HOSTS`/`build_topology`/`topology_manifest`, `POLICY_VERSION_REGISTER`,
`TrafficClass`/`TRAINING_LABELS`, `CaptureWindow`, and the sole `SwitchApi` BMv2 transport boundary.
Contracts produced: existing `CaptureWindow` objects plus runner JSONL orchestration records;
only existing capture.py writes frozen labels.csv. No frozen constants/table signatures changed.

Test-first evidence: before implementation,
`.venv/bin/python -m pytest tests/test_runtime.py tests/test_target_app.py tests/test_human_replay.py tests/test_agent_runners.py`
failed collection for the four absent harness modules. The occupied-Thrift-port test then failed
before endpoint validation was added. Host checks include actual HTTP MCP requests, seeded planning,
actual concurrent tool calls, failed-run provenance, invalid/malformed/oversized requests, trace
validation, faithful replay argv, exclusive logs, subprocess escalation, and network cleanup.

Executable interfaces and exact Linux smoke command are in `harness/README.md`. Linux M1 requires
root, Python-visible Mininet, compiled `build/l2fwd.json`, `simple_switch`, `simple_switch_CLI`,
`iperf3`, `ip`, and `tc`, an unused Thrift port, and unused topology interfaces. Validate imports with
`sudo .venv/bin/python -c 'import mininet; import harness.runtime'`; validate binaries using
`command -v simple_switch simple_switch_CLI iperf3 ip tc`. M1 smoke does not claim M4/M5 behavior.

Remaining external acceptance work: actual Linux M1 execution, Docker image build/container
namespace attachment, genuine four-framework installation/capture, consented trace acquisition and
browsing capture, packet taps, ≥95% label verification and real corpus collection. Framework labels
are not fabricated. DESIGN GAP: authoritative low/med/high burst-rate, parallelism and payload
numbers are absent from the spec and need an agreed mapping before experiment collection.

Final targeted verification: **27 tests passed** with real ephemeral loopback sockets enabled.
Targeted ruff lint/format and compileall passed. The requested repository-wide `make lint` run
passed `ruff check .`; formatting was temporarily blocked by concurrently edited controller-owned
`tests/test_ja4.py`, which this workstream did not modify. The parent integration pass owns the
final repository-wide gate.

Actual Linux smoke remains unverified. The existing Azure `p4dev` VM was deallocated and Azure
rejected the attempted start/run-command with `ReadOnlyDisabledSubscription`; that external
subscription state prevents runtime verification here. Host tests and successful target HTTP
requests do not replace a BMv2/Mininet smoke result.

Review corrections: `run_external_framework` now starts an owned process group and terminates
that entire group on timeout, interruption, and normal leader exit. A bounded SIGTERM grace
period escalates to SIGKILL for remaining descendants; unrelated processes are never targeted.
Real UDP regressions first reproduced continued descendant traffic after timeout and leader exit,
then verified traffic stopped, including a SIGTERM-resistant descendant and interrupted wait.
Framework stdout/stderr remain in the exclusive original output file; no labels are invented.

Human replay now shares `harness.capture.validate_capture` with feature extraction before Scapy
reads packets. Test-first failures demonstrated accepted truncated final headers/bodies, snapshot
length overflow, and captured/original length mismatch. Replay rejects these before invoking
`tcpreplay`. Input is complete classic Ethernet pcap; pcapng requires conversion, reported in the
error. This avoids adding pandas to the replay boundary or duplicating capture framing rules.

Review-correction verification: **19 tests passed** in the targeted agent-runner/human-replay
suite with local sockets enabled. Repository-wide `make lint` passed (74 Python files formatted).
