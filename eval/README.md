# Reproducible evaluation

`eval/run_experiment.py` is the only supported experiment entry point. It validates the frozen
design-section 4.6 YAML schema, expands share × burst × seed deterministically, acquires one
host-wide exclusive lock, and creates append-only directories under:

```text
<outputs>/<full-config-sha256>/<system>-share-<pct>-burst-<preset>-seed-<seed>/
```

Each directory starts with `manifest.json`, including the full cell coordinates and exact setup,
workload, and teardown argument vectors. Failed cells remain in place with `status: failed`; never
delete or silently reuse them. Correct the cause and run a new named config.

## Commands

Validate without side effects:

```bash
python -m eval.run_experiment eval/configs/burst_sweep_v1.yaml
```

Materialize a dry-run matrix in a disposable root:

```bash
python -m eval.run_experiment eval/configs/fifo_sanity.yaml \
  --execute --dry-run --project-root /tmp/agentqos-dry-run
```

Execute one config inside the documented Linux VM as root (Mininet requires it):

```bash
make preflight CONFIG=eval/configs/fifo_sanity.yaml
make experiment CONFIG=eval/configs/fifo_sanity.yaml
```

`eval.preflight` is read-only. It checks Linux/root execution, required commands, the Mininet Python
module, the selected compiled P4 JSON, the deterministic task script, Dev A/Dev B files for the
proposed system, and every append-only cell path. `make experiment` runs this gate before it creates
the first result directory.

The executor starts one persistent programmed topology, installs the selected baseline inside that
topology, captures pcap plus line-buffered packet telemetry, runs the paced human flow and four
concurrent agent sources, serves the live dashboard on port 8088, writes labels and metrics, and
then tears every process down. A manifest becomes `complete` only after `run_summary.json` exists,
matches the cell coordinates, every training class has positive matched two-tap evidence, and SHA-256
digests for the summary plus both telemetry files are recorded. Runtime failure or missing evidence
leaves an immutable `failed` manifest.

FIFO, DiffServ, fairq, and app-limiter cells use `build/l2fwd.json`. The proposed-system cell uses
`build/agent_aware.json`. Its workload is wrapped in a long-running `controller.app` process; the
run must observe a positive `policy_version`, then preserve and hash `controller_lifecycle.json`.
The current Dev B probe exits immediately and the policy/punt/reclassifier modules are absent, so
preflight/runtime correctly block proposed-system evidence until that integration lands.

The four baseline sanity definitions are `fifo_sanity.yaml`, `diffserv_sanity.yaml`,
`fairq_sanity.yaml`, and `app_limiter_sanity.yaml`. The complete 375-cell matrix is defined by
`burst_sweep_v1.yaml`, `fifo_burst_sweep_v1.yaml`, `diffserv_burst_sweep_v1.yaml`,
`fairq_burst_sweep_v1.yaml`, and `app_limiter_burst_sweep_v1.yaml`: five systems × five shares ×
three burst presets × five seeds. Never edit a config between runs.

Each successful cell contains the manifest, storm plan, pcap, target and ingress packet telemetry,
MCP and iperf logs, orchestration windows, per-framework attempt results, labels, corpus statistics,
final run summary, and dashboard snapshot/logs. Packet identities are matched across the
class-host-facing switch ports and target interface; summaries record match coverage and cannot
complete with ACK RTT fallback. Request rejection is a measured outcome: adapters record failed
attempts but exit successfully after completing their assigned work, so the app-limiter baseline is
not incorrectly treated as a harness crash.

Run all four baseline sanity configs, then the complete grid only after every preflight is green:

```bash
sudo make baseline-sanity
sudo make full-grid
```

## Aggregate completed runs

After all five multi-seed grids complete, aggregate them before plotting:

```bash
make aggregate RESULTS=results AGGREGATES=results/aggregates
```

Aggregation verifies each manifest-to-summary/telemetry SHA-256 link and coordinate set. Single-seed
sanity configs remain in `audit.csv` but are excluded from statistical rows. Incomplete, failed, or
post-run-mutated evidence is rejected rather than silently omitted. Outputs are `run_metrics.csv`,
`confidence_intervals.csv`, `centerpiece.csv`, and `audit.csv`. The centerpiece automatically uses
the largest common high-burst agent share, requires the same five-or-more seed set for all systems,
requires a human two-tap match in every one-second interval, and averages human p99 across seeds.

## Evaluate the predeclared anchors

After Dev A provides the audited `overhead.csv`, evaluate both headline targets explicitly:

```bash
make anchors AGGREGATES=results/aggregates
```

The command averages each balanced centerpiece timeline, compares `ours` against the baseline with
the lowest mean human p99, and compares mean full-pipeline latency with mean minimal-l2fwd latency.
It writes `anchors.json` with both input SHA-256 digests, thresholds, measurements, and pass flags.
The output is append-only. A missed target still writes the report and returns status 2 so CI or an
operator cannot hide the failure. The predeclared 30% reduction and 5% overhead thresholds are
fixed in the evaluator. Overhead input requires minimal/full samples paired by pair, seed, host,
load profile, and sample identity across at least five seeds.

## Aggregate figure inputs

`make figures RAW_RESULTS=<dir> FIGURES=<dir>` requires these immutable CSVs:

| File | Required columns |
|---|---|
| `centerpiece.csv` | `time_s,system,human_p99_ms` |
| `classification.csv` | `class,precision,recall` |
| `overhead.csv` | `pair_id,seed,host_id,load_profile,sample_id,pipeline,latency_ms` |
| `scaling.csv` | `concurrent_flows,accuracy,memory_bytes,collision_rate` |
| `evasion.csv` | `think_time_ms,accuracy,throughput_tps` |
| `feature_importance.csv` | `feature,importance` |

The generator validates every input before creating the output directory and never edits source
data. It writes stable Fig. 2–6 PNG names and a sorted feature-importance CSV. Aggregation from raw
per-seed manifests into these inputs must retain config hash and seed columns in its audit trail.

No checked-in fixture is a research result. Do not publish latency, precision/recall, overhead, or
anchor claims until the Linux grid has run and every plotted row traces back to a manifest.
