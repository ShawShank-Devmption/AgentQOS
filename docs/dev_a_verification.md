# Dev A implementation and verification — 2026-10-31

This records C2, C3, C4/P4.3, C1, D4/P3.13–P3.14 and G2 from the handoff. It does not claim
live measurement or Linux verification. Team approval of the C1 proposal was confirmed by the
project owner in this session. No messages were sent to teammates and no PRs were published.

## Scope and review units

Keep these as separate task PRs when publishing; shared-file hunks must be staged per task.

| Task | Implementation | DoD verification / remaining work |
|---|---|---|
| C2 | `tests/parser_cases.py`, `tests/ptf/test_parser.py`, `tests/test_parser_cases.py` | Host test requires all 24 cases to have sendable Ethernet headers and verifies the 14-byte IPv4 truncation case. Run parser PTF on the VM; log must show OK. |
| C3 | `tests/ptf/test_agent_pipeline.py` | Check sketch 0 before send, 1 after ClientHello, 2 after a second valid flow, unchanged after malformed TLS and fragment. Real BMv2 PTF remains required. |
| C4 | `harness/runtime.py`, `eval/overhead.py`, `eval/overhead_report.py`, `overhead_*.yaml`, associated pytest | Fixed-rate seed schedules, one-core launch, CPU/clone diagnostics, artifact footprint, paired comparison. Full sweep, cause/fix, refined passing boundary and review_1_smoke rerun remain unmeasured. |
| C1 | `common/contracts.py`, design §4, experiment loader, generator, topology/contract tests, separate gateway artifacts | Owner confirmed team approval. Gateway flag ABI and legacy topology are covered by host tests; real p4c/PTF compilation/behavior remain required. |
| D4 | `tests/ptf/test_qos.py`, `eval/qos_report.py`, associated pytest | Real queue set/clear, isolated preset selection/red tests; target-before-run and measured paired report. PTF and D2/D3/C4 readiness plus the demo comparison are pending. |
| G2 | `harness/gateway.py`, `gateway_*.yaml`, `tests/test_gateway.py`, `tests/ptf/test_gateway.py` | Port/queue/flood commands and cleanup tested on host; startup rejects missing I3 scripts/shaping or original artifacts. I3 integration, compiled-artifact compatibility, tunnelled iperf and <5 s bypass recovery remain pending. |

## C2/C3 findings

C2 replaces the 13-byte `short_ethernet` case (Linux AF_PACKET rejects it with EINVAL) with
`ethernet_only_ipv4`: the full 14-byte Ethernet header advertising IPv4 but no IPv4 bytes.
All other cases remain. Packet data moved into a PTF-independent helper so plain pytest can
inspect it without installing/running PTF. The PTF assertion still requires unchanged frame
forwarding, feature_valid=0, and UNKNOWN class (§7.15).

C3's test sent a valid ClientHello on source port 40000, then malformed TLS on source port
40002 **from the same source IP**, then asserted that the per-source sketch was still 1.
The malformed packet is a valid new L4 flow and the same test explicitly confirms that it
claimed a slot. `update_sketch` increments new flows per source, not just valid ClientHellos.
Thus 2 is the expected value at that point; it is not evidence of duplicate ingress updates.
The test now checks before and after each send, preserving §7.10 epoch assumptions and the
§7.12 first-packet counter check. No data-plane update was removed or bypassed. Confirm this
ordering diagnosis on BMv2 before declaring the Linux failure resolved.

## Compiler and PTF gate

`make build` was attempted locally and failed because `p4c-bm2-ss` is not installed. The P4
increment is limited to separate gateway build mode, independent direction flags, and reflected
flood suppression. Per AGENTS.md, do not extend this P4 code until a developer runs real p4c
and supplies the compiler output. No compiler success or PTF pass has been inferred from
host-side source tests.

The existing PTF launcher on the VM must use **fresh switches per suite**:

- `test_parser.ParserForwardingTest`: isolated parser observation harness.
- `test_agent_pipeline.AgentPipelineSmokeTest`: original agent_aware artifact, ports 1/4/255.
- `test_qos.QosProtectionTest`: original agent_aware artifact, ports 1/4; no live controller.
- `test_gateway.GatewayForwardingTest`: run twice, with fifo_gateway and agent_gateway;
  pass `system='fifo'` / `system='ours'`, ports 1/4. No live controller.

The QoS suite logs real-queue flag transitions and separately labelled **isolated** meter
stimuli. It restores the frozen class policy afterwards. Zero-rate test meters and exact
flow overrides are not production tuning or proof of live classification. No new hardware
counter names are introduced. Live workload meter-drop counters remain unavailable until
separately instrumented/approved; do not equate isolated counts with production counters.

## C4 execution and reports

The sweep commands and measurement limits are in `docs/notebook.md`. Generate a new report
from the exact completed cell directories (paths containing config hash, seed and share):

```sh
.venv/bin/python -m eval.overhead_report \
  results/overhead_100_fifo/<hash>/seed-1/share-30_burst-low \
  results/overhead_100_ours/<hash>/seed-1/share-30_burst-low \
  --output results/overhead-report-<new-run-id>
```

Reports reject failed runs, hash mismatches, different hosts/CPU/binary/profile/model or
per-system pipelines, different packet schedules, and incomplete sender schedules. They
report aggregate p50/p99, achieved offered pps, CPU, emitted CPU-port clones, loss, HUMAN p99
ratio, footprint and the highest tested loss-free rate meeting the latency/punt criteria.
This is a sampled boundary, not a proof of a global maximum or hardware performance.
Review sender cadence and host jitter before selecting a demo rate. Never claim a safe rate
merely because a lossy run has low latency among its surviving packets.

## D4 target and report

Only run the demo comparison after C4 is measured and D2/D3 traffic/classification are verified.
State the HUMAN target before running either system; for example, to explicitly choose 50 ms:

```sh
.venv/bin/python -m eval.qos_report declare-target --target-ms 50 \
  --output results/<new-run-id>-target.json
# Run the approved demo_call_fifo and demo_call_ours through eval.run_experiment.
# Use the verified D2 traffic profile, same seed/plan and trained model; no label-forcing overrides.
.venv/bin/python -m eval.qos_report report \
  --target results/<new-run-id>-target.json --ours <ours-cell-dir> --fifo <fifo-cell-dir> \
  --ptf-log <fresh-qos-ptf-log> --output results/<new-run-id>-qos-report.json
```

50 ms above is an example declared target, not a measured result or approved tuning value.
The report checks timestamps, run success/observation hashes, matching hosts/cells/plans,
checkpoint-16 HUMAN/BULK predictions, real queue transition logs and isolated meter behavior.
Missing latency stays unavailable; missing PTF/classification evidence cannot pass. It also
reports HUMAN loss and refuses to pass an ours run losing HUMAN packets. Production preset
retuning remains a separate contracts change. NFR-1 versus the **best of four baselines** is a
separate full evaluation; FIFO comparison alone does not establish that anchor.

## G2 integration and activation

DESIGN GAP: I3 is absent from this checkout. The required handover interface is documented in
§4: `netsetup.sh --switch` must detach bypass only, preserve the interfaces and tc shaping,
and `bypass.sh` must restore forwarding idempotently without removing aggregate shaping.
The I3 owner must implement/confirm that behavior before activation. G2 does not create,
replace, or delete I3's links, namespaces, WireGuard setup or NAT rules.

The launcher verifies Linux/root/tools, I3 scripts, MTUs, aggregate tc qdisc presence, peer
MACs, an unoccupied Thrift endpoint, and gateway-specific compiled ABI before taking traffic.
It configures BMv2 shaping on both ports, but per-queue packet-rate limits are **not** an
aggregate byte-rate guarantee. I3 must supply both aggregate tc caps; verify the exact rates
with tunnelled iperf before the demo. A qdisc-presence check alone is not rate verification.

After compiler/PTF gates and I3 integration, deploy a new session:

```sh
sudo env PATH="$PATH" PYTHONPATH="$PWD" .venv/bin/python -m harness.gateway \
  --config eval/configs/gateway_fifo.yaml --output results/<new-fifo-session>
# Repeat with gateway_ours.yaml and a new output directory.
```

The launcher is deployment lifecycle tooling, not a measured experiment runner. G3's controller
is a separate dependency; the ours pipeline starts fail-open until policy/classification is
installed. Collect G4's real observations and run evaluation through eval.run_experiment.
Kill the owned switch during tunnel traffic and measure recovery, including I3's bypass
execution, below five seconds. Host tests verify call order and prompt exit detection; they
do not measure network recovery. Normal session completion, interrupt, launch failure, policy
failure and switch death all restore bypass after stopping the owned process.

## Final host checks

- `make lint`: passed (108 Python files).
- `make check-generated`: passed.
- `make test`: **534 passed, 6 skipped** (9.44 s). Five skipped PTF modules need Linux BMv2;
  one existing host-dependent test also skipped.
- All YAML configs passed `eval.run_experiment.load_config`.
- `git diff --check`: passed.
- `make build`: unavailable (`p4c-bm2-ss` missing); live/PTF acceptance remains outstanding.

## Additional Dev A tasks

P4.4 now has trace preparation, a live experiment-runner adapter, collision/concurrency
analysis, RSS and logical memory reporting, and Fig. 5 aggregation/rendering. See
`docs/state_scaling.md`. The analytical 25-cell hash plan ran on the host; the live sweep
still requires the authenticated Linux VM and compiled artifacts.

P5.1's design, implementation, state-scaling method and limitations sections are written as
`docs/paper_dataplane.tex`, for inclusion in the team's IEEE manuscript. They describe the
actual Thrift/AF_PACKET daemon and retain unverified targets as targets. No result numbers
are fabricated. The existing Word paper was not modified; the specified deliverable is IEEE
LaTeX. This host has no LaTeX compiler, so full-manuscript compilation remains pending.

DESIGN GAP: §7.15 says malformed TLS causes no register writes; the existing parser/pipeline
fixture expects a flow claim and PROTO_META_PAYLOAD_SEEN update for malformed TLS carried in
valid TCP. C3's corrected counter placement does not settle this conformance issue. Preserve
the frozen contract when preparing the eventual parser fix, and obtain real compiler output
before extending the current uncompiled P4 increment.


Follow-up on §7.15: the updated pipeline PTF fixture now requires malformed TLS to leave
reg_flow_key, reg_pkt_count and reg_proto_meta untouched. A separate valid TCP flow supplies
the second sketch increment. This supersedes the earlier test expectation that malformed
TLS could legitimately claim state. The current P4 is expected to need a fix; that change
remains blocked on the required compiler/VM step, so C3 is not accepted as complete.

Latest host verification after P4.4/P5.1: **552 passed, 6 skipped**; lint (113 Python files),
generated-file checks and YAML validation passed. Google Cloud CLI 591.0.0 is installed and
its login browser flow was opened; VM access now requires the user's Google authentication
and project permission. The absence of live measurements, I3 integration and compiler/PTF
acceptance remains explicit. Do not label all Dev A tasks complete until those checks pass.

Cloud authentication subsequently completed. The read-only VM describe call was rejected
for missing `compute.instances.get` on vivid-rush-513007-k5 / asia-south2-a / agent-qos-p4dev.
Grant the authenticated user's project/VM access before continuing Linux verification.
