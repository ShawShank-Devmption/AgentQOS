# Agent-Aware Networking

Research prototype for classifying AI-agent traffic in a P4/BMv2 switch and coupling the class to
QoS treatment that protects human interactive traffic during machine-paced bursts.

The repository currently contains the complete Dev C traffic, baseline, experiment, live-dashboard,
metrics, aggregation, and figure-generation path. Host-safe tests verify those boundaries. Live
Mininet/BMv2 results are not checked in, and no headline performance claim is made here.

## Host-safe verification

Python 3.11 is required. Create `.venv` and install the approved packages listed in
[`docs/ENVIRONMENT.md`](docs/ENVIRONMENT.md), then run:

```bash
make lint
make test
python -m eval.run_experiment eval/configs/fifo_sanity.yaml
```

These commands validate code, contracts, metric math, configs, and orchestration without claiming
that the Linux data plane ran.

## Linux baseline sanity run

Mininet and BMv2 require the pinned Ubuntu environment in
[`docs/ENVIRONMENT.md`](docs/ENVIRONMENT.md). From an otherwise idle VM:

```bash
make dev-env
make build
sudo make smoke-m1
sudo make preflight CONFIG=eval/configs/fifo_sanity.yaml
sudo make experiment CONFIG=eval/configs/fifo_sanity.yaml
```

While the cell runs, the live dashboard is available at `http://127.0.0.1:8088`. Results are
append-only below the config's `outputs` path. A successful manifest is backed by captured traffic,
orchestration-derived labels, matched two-tap per-class telemetry and coverage, agent attempt
counts, and SHA-256-attested summary plus target/ingress telemetry. Preflight is read-only and
reports Linux/root, command, Mininet, compiled-program, task-script, Dev A/Dev B, and
append-only-path blockers before a long run creates output.

The FIFO, DiffServ, fairq, and nginx application-limiter baselines use `build/l2fwd.json`. The
proposed system additionally requires Dev A/Dev B to provide `p4src/agent_aware.p4`, its compiled
JSON, policy installation, and the live controller lifecycle. Until those exist, do not present an
`ours` dry run or storm plan as experiment evidence.

## Full evaluation and figures

The five `*_burst_sweep_v1.yaml` configs define 375 cells: five systems × five agent shares × three
burst presets × five seeds. Run each config through `eval.run_experiment`, then aggregate completed
evidence:

```bash
sudo make baseline-sanity
# Only after the proposed-system integration preflight passes:
sudo make full-grid
make aggregate RESULTS=results AGGREGATES=results/aggregates
# After Dev A supplies the audited overhead.csv:
make anchors AGGREGATES=results/aggregates
```

`eval.aggregate` rejects incomplete runs and hash/coordinate mismatches, emits per-run metrics and
Student-t confidence intervals, and creates the balanced five-system centerpiece timeline. The
remaining figure inputs come from the Dev A/Dev B classification, overhead, scaling, evasion, and
feature-importance experiments. `eval.anchors` selects the lowest-latency baseline, attests both
input files, writes the failed result as evidence, and returns nonzero if either predeclared target
is missed. When all six strict CSV inputs are present:

```bash
make figures RAW_RESULTS=results/aggregates FIGURES=results/figures
```

See [`eval/README.md`](eval/README.md) for result schemas and commands,
[`harness/README.md`](harness/README.md) for workload/corpus rules, and
[`docs/demo_rehearsal.md`](docs/demo_rehearsal.md) for the two required rehearsals.

## Repository map

- `common/`: frozen cross-module contracts from design section 4.
- `p4src/`, `controller/`, `ml/`: Dev A/Dev B data-plane, control-plane, and ML scope.
- `harness/`: topology, MCP target, traffic sources, capture, and persistent storm runtime.
- `eval/`: baselines, immutable experiment runner, metrics, aggregation, configs, and plots.
- `dashboard/`: live packet-metric producer and read-only web UI.
- `tests/`: host pytest coverage plus the PTF location for P4 tests.

The authoritative requirements, architecture, assignments, and engineering rules are
[`requirements.md`](requirements.md), [`design.md`](design.md), [`tasks.md`](tasks.md), and
[`AGENTS.md`](AGENTS.md).
