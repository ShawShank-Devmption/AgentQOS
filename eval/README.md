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

The live entry point is reserved, but intentionally fails before running baseline setup today:

```bash
make experiment CONFIG=eval/configs/fifo_sanity.yaml
```

Persistent execution depends on the Person 1/2 `agent_aware.p4` classifier/QoS pipeline and its
controller lifecycle, which are not present in this checkout. The failure is deliberate: a storm
plan is not experiment evidence. Once that dependency lands, the executor must start the topology
before installing baseline commands, drive the complete duration, collect pcaps/logs, and only then
allow a manifest to become `complete`.

The four baseline sanity definitions are `fifo_sanity.yaml`, `diffserv_sanity.yaml`,
`fairq_sanity.yaml`, and `app_limiter_sanity.yaml`. The five-seed proposed-system grid is
`burst_sweep_v1.yaml`; create equivalent frozen-schema configs for each baseline rather than
editing a config between runs.

## Aggregate figure inputs

`make figures RAW_RESULTS=<dir> FIGURES=<dir>` requires these immutable CSVs:

| File | Required columns |
|---|---|
| `centerpiece.csv` | `time_s,system,human_p99_ms` |
| `classification.csv` | `class,precision,recall` |
| `overhead.csv` | `pipeline,latency_ms` |
| `scaling.csv` | `concurrent_flows,accuracy,memory_bytes,collision_rate` |
| `evasion.csv` | `think_time_ms,accuracy,throughput_tps` |
| `feature_importance.csv` | `feature,importance` |

The generator validates every input before creating the output directory and never edits source
data. It writes stable Fig. 2–6 PNG names and a sorted feature-importance CSV. Aggregation from raw
per-seed manifests into these inputs must retain config hash and seed columns in its audit trail.

No checked-in fixture is a research result. Do not publish latency, precision/recall, overhead, or
anchor claims until the Linux grid has run and every plotted row traces back to a manifest.
