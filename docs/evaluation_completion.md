# Evaluation host implementation

Tasks P3.12, P4.1/P4.2/P4.6/P4.8 consume design §4.6, the frozen class labels and
FEATURE_CHECKPOINTS from common/contracts.py, and independently measured observations. No frozen
contract has changed. Host tests exercise the command boundary with explicit hand-checked fixtures;
those fixtures are not performance results.

## Commands and APIs

From the project root with the installed Python environment:

```sh
python -m eval.run_experiment eval/configs/sanity_fifo.yaml --plan
python -m eval.run_experiment eval/configs/sanity_fifo.yaml --execute /absolute/path/to/real-workload
python -m eval.metrics RUN_DIR/observations.json --output NEW_SUMMARY.json
python -m eval.metrics --runs RUN_DIR_1 RUN_DIR_2 RUN_DIR_3 RUN_DIR_4 RUN_DIR_5 --output NEW_AGGREGATE.json
python -m eval.plots RUN_DIR/observations.json --output-dir NEW_FIGURE_DIRECTORY
python -m eval.plots RUN_DIR/summary.json --output-dir ANOTHER_NEW_FIGURE_DIRECTORY
```

The `--execute` argument consumes the remaining argv verbatim, without a shell. It is an explicit
real command boundary, not a simulator. The executable must implement and verify the requested
system, own its network setup/cleanup, use the given random seed, retain raw pcaps/logs in the output
directory, and export observations. Zero exit status without valid measured packets fails the run.

`execute_experiment(config, command, project_root, lock_path=None)` returns the completed run paths.
The default fixed host-wide lock is `/tmp/agent-aware-networking-experiment.lock`; its inode is
retained after release to avoid an unlink race (§7.19). `lock_path` is for isolated API tests; CLI
execution always uses the host-wide default. Each workload gets these environment variables:

| Variable | Meaning |
|---|---|
| AAN_CONFIG_PATH | Canonical, preserved frozen-schema YAML snapshot |
| AAN_CONFIG_HASH | SHA-256 of canonical config values, including sorted grid dimensions |
| AAN_SYSTEM / AAN_TOPOLOGY | Requested system and topology |
| AAN_LINK_MBPS | Bottleneck bandwidth |
| AAN_SEED | Independent random seed for this invocation |
| AAN_AGENT_SHARE_PCT / AAN_BURST_INTENSITY | Current grid cell |
| AAN_DURATION_S | Requested measurement duration |
| AAN_OUTPUT_DIR | Absolute directory owned by this invocation |
| AAN_OBSERVATIONS_PATH | Absolute `observations.json` destination |

Each cell lives at `outputs/CONFIG_HASH/seed-N/share-P_burst-B/`. Exclusive directory creation
prevents overwriting successful, failed, or interrupted evidence. `started.json`, `config.yaml`,
stdout/stderr logs, and the workload's raw artifacts remain preserved. `completed.json` records
success/failure, return status, timestamps, and errors; success includes the raw-observation SHA-256.
The runner imposes `duration_s + 60` seconds for runtime startup and measurement, then terminates
only its own process group. Runtime commands must clean their owned Mininet/container resources;
the host runner does not globally kill other experiments or delete results. A hard host kill can
leave only a started manifest, which is intentionally ineligible for aggregation.

`plan_grid`, `config_hash`, `load_config`, `validate_config`, `summarize_observations`,
`seed_confidence_interval`, `aggregate_runs`, `assert_anchor_targets`, and `render_figures` are
available as Python APIs. Duplicate YAML/JSON fields and duplicate grid dimensions are rejected.

## Raw observation format

DESIGN GAP: §10 specifies measurement sources but no interchange format. The following strict
local observation format fills that gap without altering §4.4 labels or §4.6 config. It should be
reviewed at team sync before other producers adopt it. Timestamps are seconds on a synchronized
clock. All quantities must be finite JSON numbers; booleans are not numeric measurements. Fields
are exact: missing or extra fields fail validation.

```json
{
  "measurement_start_s": 0.0,
  "measurement_end_s": 2.0,
  "packets": [
    {"packet_id": "tap-pair-unique-id", "traffic_class": 1,
     "sent_s": 0.1, "received_s": 0.12, "size_bytes": 1000}
  ],
  "tool_calls": [
    {"call_id": "target-log-request-id", "start_s": 0.2, "end_s": 0.5}
  ],
  "classifications": [
    {"flow_id": "orchestrated-flow-id", "checkpoint": 6, "truth": 1,
     "predicted": 0, "label_source": "orchestration"}
  ]
}
```

`packets` must be nonempty. Each packet ID must uniquely match two taps or instrumentation records;
it is not a label feature. `traffic_class` groups the independent ground-truth workload class, not
the classifier prediction. `received_s: null` means not observed at the receive tap by window end;
this is counted as lost/censored and excluded from latency percentiles. Bytes of received packets
produce throughput over the entire measurement window. No samples for a class means unavailable
latency/throughput, not a measured zero. Timestamps must be ordered and inside the window.

Target-observed `tool_calls` may be empty; `end_s: null` means incomplete. Completion percentiles
include completed calls only, accompanied by started/completed/incomplete counts and completion
fraction to expose survivorship and potential starvation. Empty lists yield unavailable metrics.
Classification rows must have independent orchestration provenance (§7.20), unique flow/checkpoint
pairs, invariant truth across checkpoints, truth in the three labeled classes, prediction in the
four frozen classes, and checkpoints only 6/16/64. Unlabeled background is excluded from P/R.
Unknown predictions count as misses for their true class. Missing denominators yield null P/R.

## Statistical and anchor behavior

Per-run p50/p95/p99 use linear interpolation at `(n - 1) * quantile`. Packet latency and tool
completion are milliseconds; throughput is Mbps. Aggregation verifies success manifests, canonical
config hashes and raw hashes, and recomputes summaries from raw observations. Runs group by config
hash and share/burst cell. Each cell requires every configured seed exactly once and at least five
seeds. Duplicate paths/seeds, missing seeds and failed runs are rejected. A metric unavailable in
any seed has a null aggregate estimate plus explicit `unavailable_seeds`; it is never averaged over
a selectively successful subset.

CIs are two-sided 95% Student-t intervals over independent seed values, not packet samples. SciPy
is already a transitive scikit-learn runtime dependency; no dependency was added. The anchor API
requires all four measured baseline p99 values and comparable measured full-pipeline/l2fwd latency:

```python
assert_anchor_targets(ours_p99, baselines_p99, pipeline_latency, l2fwd_latency)
```

It chooses the lowest (best) baseline p99 and asserts reduction >= 0.30 and added latency < 0.05.
Missing/nonpositive inputs fail validation. Missed anchors log FAIL and raise `AnchorError`. The
caller must use matching topology/load and pinned hardware (§7.18); this function cannot establish
those experimental conditions from scalar inputs.

## Actual deliverables versus external evidence

`sanity_{ours,fifo,diffserv,fairq,app_limiter}.yaml` are valid frozen-schema, 30-second, five-seed
configurations. The baseline directory documents the exact runtime prerequisites. No system-name
mapping silently substitutes another algorithm, and no baseline performance has been fabricated.
A real workload executable and verified Linux system implementations are still required to run
these configurations and satisfy the P4.1 baseline-runtime acceptance criterion.

Plots regenerate four deterministic PNG diagnostics (latency, throughput, tool completion,
checkpoint classification) from raw observations or validated per-run summaries, preserving source
bytes and marking unavailable data explicitly. They do not claim to be the final comparative,
overhead, scaling or evasion paper figures. Those require the real multi-system grid and additional
resource/evasion measurements. No headline anchor, line-rate accuracy, starvation result, or Linux
baseline correctness claim follows from host fixture tests.

## Two-tap pcap adapter (P3.12)

`eval/pcap_metrics.py` consumes the frozen §4.4 orchestration label CSV and converts actual
classic Ethernet pcaps into the strict observations above. It introduces no contract changes.

```sh
python -m eval.pcap_metrics ingress.pcap egress.pcap labels.csv \
  --start-s 100 --end-s 101 --tool-log target.jsonl --output NEW_OBSERVATIONS.json
```

The equivalent API is
`observations_from_pcaps(ingress: Path, egress: Path, labels: Path, start_s: float,
end_s: float, tool_log: Path | None = None) -> dict[str, Any]`. It validates the complete result
through `summarize_observations`; the CLI exclusively creates its output and returns nonzero on
invalid evidence or an existing output. Original captures, labels and logs are read only.

The two inputs are direction-aware logical sender/receiver taps: **each** must contain both flow
directions, with each packet's pre-path observation in ingress and post-path observation in egress.
Two ordinary bidirectional captures on opposite physical interfaces must first be separated and
merged by packet direction; treating interface sides as sender/receiver would reverse response
latencies. Capture before flow startup, use synchronized clocks and loss-free capture instrumentation,
and preserve full Ethernet records. The adapter cannot establish clock synchronization or distinguish
capture loss from network loss on its own.

Packet matching uses the full IPv4 datagram after normalizing DSCP/ECN, TTL and IPv4/TCP/UDP
checksums; L2 addresses and Ethernet padding are outside the identity. A SHA-256 of that identity is
the observation packet ID. Repeated normalized datagrams within either tap are rejected rather than
paired speculatively; instrument such traffic with unique on-wire packet IDs before measuring it.
Egress packets without ingress matches, negative delays, unordered capture timestamps, malformed
headers, fragmentation, VLAN encapsulation, unsupported IPv4 transports, invalid record lengths,
truncation, pcapng and non-Ethernet link types fail validation. Non-IPv4 Ethernet control frames
(for example ARP) are excluded from the IPv4 metric corpus. Ethernet ingress bytes, excluding any
uncaptured physical-layer overhead, supply packet sizes.

An orchestration row applies to its forward and reverse five-tuples and must name the ingress
pcap basename. Unlabeled traffic has UNKNOWN ground truth and is excluded from classification P/R.
DSCP supplies predictions only, by reversing `CLASS_TREATMENTS`; unrecognized DSCP becomes UNKNOWN.
The 6/16/64 ordinal counts both directions in the entire ingress capture, including the prefix
before the measurement window. Reordering preserves the checkpoint packet's identity. Missing or
late receipts remain null and do not manufacture checkpoint predictions; later packets retain their
original ordinal. A classification sample requires both checkpoint ingress and receipt inside the
window. These samples observe treatment at packet ordinals; pcap evidence alone cannot verify slot
collisions, register resets, classifier installation or line-rate behavior.

Optional tool logs must use the exact JSONL fields emitted by `harness/mcp_target/server.py`:
`source_ip,start_time_s,end_time_s,status,method,tool`. Only `tools/call` starts inside the window
enter the denominator. HTTP failures and completions beyond window end are censored; 2xx records
within the window produce target-observed completion durations. Each call ID is its stable input
line number. Duplicate JSON fields, extra fields, invalid addresses and malformed numeric/status
values fail fast. Log records may arrive in completion order under concurrent requests.

DESIGN GAP: the existing target writes one record in its request-finalization block, without a
separate durable start record or request ID. Requests interrupted before that record is written are
unobservable to this adapter; a 2xx HTTP status also cannot reveal an application failure absent
from the target schema. Complete starvation/accounting claims require independent durable start
instrumentation and application-success evidence. The adapter does not infer missing calls from
packet payloads or claim those counts are complete.

The hand-created pcap fixture has 16 alternating-direction 154-byte packets, independent
orchestration labels, known 1–16 ms delays and a 100 ms tool duration. Tests verify the hand-derived
latency/throughput values, marking/checksum/TTL-insensitive joins, loss/reordering/window semantics,
UNKNOWN predictions, malformed evidence, and preservation of existing output. These are host
correctness tests, not measured baseline or headline performance evidence.
