# State scaling implementation and execution

P4.4 consumes design §10 and §7.1/§7.4/§7.11/§7.19/§7.20, the existing flow hash and tag,
reg_collision_ctr, fixed checkpoints and class labels. No frozen schema or P4 program changes
are introduced by this task. The separate JSON workload profile follows the existing approach
of eval/configs/workload_runtime.json; flow count is an explicit archived workload argument,
not an invented field in the frozen experiment YAML.

## What is implemented

- `eval/state_scaling.py`: strict profile validation, seeded distinct tuples up to 100,000,
  a first-owner hash diagnostic, immutable pcap preparation and orchestration CSV labels,
  actual two-tap analysis, offered-concurrency/refresh measurements, observed collision
  packet counts, checkpoint accuracy, occupied-slot counts and compiled register footprint.
- `eval/state_scaling_live.py`: a real Linux workload adapter for eval.run_experiment. It
  replays pre-generated traces inside the agent namespace, captures switch ingress/egress,
  reads fresh/stable switch state through SwitchApi, records switch RSS and CPU/binary
  identity, and cleans up owned processes. It never substitutes the planned hash diagnostic
  for real switch measurements.
- `eval/state_scaling_plots.py`: Fig. 5 PNG/PDF and a numeric aggregate from complete live
  seed/count cells. It checks source hashes, host/CPU/binary/pipeline/model consistency and
  seed completeness. A concurrency failure in any seed makes that flow-count point
  unavailable; it is not omitted to obtain a favorable average.
- Five YAML experiments define 1k, 10k, 25k, 50k and 100k flow cells with five explicit seeds.
  The local profile defines packet count, payload, offered rate, drain and destination port.

The generated flows have identical UDP stress schedules; class labels are assigned by seeded
orchestration before any feature inspection, with the configured packet/flow share. They are
**synthetic stress labels**, not evidence of real human/agent separability. All flows use the
same physical sender and source MAC with distinct synthetic source IPs; the generator does
not open 100,000 sockets or claim that these source IPs are real devices. The target binds
the UDP destination port to suppress kernel port-unreachable replies. This sink is not a
throughput measurement; the two switch taps supply the actual packet observations.

## Host execution already performed

The 25-cell analytical ownership plan was generated locally:

```sh
.venv/bin/python -m eval.state_scaling prepare-sweep \
  --experiment eval/configs/state_scaling_1000.yaml \
  --profile eval/configs/state_scaling.json \
  --output results/state-scaling-plan-dev-a-20261031 --plan-only
```

Its provenance is `analytical_hash_plan_not_switch_measurement`; measured accuracy and
collision events are explicitly null. It is not Fig. 5 data. Results remain outside git.
The host tests use small fabricated captures to test the analysis boundary and figure
rendering; those fixtures are not performance results either.

## Linux preparation and live sweep

Prerequisites: p4c/BMv2/Mininet, tcpdump, tcpreplay, the existing Python dependencies, and a
trained equivalence-gated compiled tree. Compile and pass PTF first. Keep the VM idle except
for this experiment, and select an available CPU. Use a fresh results root for every rerun.
The prepare command writes about 5.6 million packets across the configured five seeds/counts;
allow preparation time and disk space before launching the timed experiment runner.

```sh
# Input generation is not a network experiment. This command prepares all configured seeds/counts.
.venv/bin/python -m eval.state_scaling prepare-sweep \
  --experiment eval/configs/state_scaling_1000.yaml \
  --profile eval/configs/state_scaling.json --output results/scaling-inputs-run-1

# One flow-count experiment, all seeds. Select a CPU allowed by taskset -pc $$.
export TASK_SWITCH_CPU=0
export AAN_COMPILED_TREE=/path/to/equivalence-gated/compiled_tree.json
sudo env PATH="$PATH" PYTHONPATH="$PWD" AAN_COMPILED_TREE="$AAN_COMPILED_TREE" \
  .venv/bin/python -m eval.run_experiment eval/configs/state_scaling_1000.yaml \
  --execute .venv/bin/python -m eval.state_scaling_live \
  --inputs results/scaling-inputs-run-1 --flows 1000 --switch-cpu "$TASK_SWITCH_CPU"
```

Repeat using the other four state_scaling YAMLs and their matching flow-count argument;
input files already contain all configured counts. The frozen runner owns the host lock,
config hash, per-seed output directories, completion status and observation hashes.
The runtime owns its independent choke_v1 interface lock. Do not launch another network
or build/replace a pipeline while the sweep is active.

To aggregate, pass the 25 exact completed cell directories to the plot command:

```sh
.venv/bin/python -m eval.state_scaling_plots <completed-cell-dir-1> <completed-cell-dir-2> ... \
  --experiment eval/configs/state_scaling_1000.yaml \
  --profile eval/configs/state_scaling.json --output results/scaling-figure-run-1
```

These angle-bracket paths are placeholders, not measured or claimed result directories.
Both PNG and PDF are standalone figure exports; scaling-aggregate.json contains the means,
95% seed confidence bounds, unavailable points and logical register storage.

## Interpretation and acceptance

A register collision is a **packet event**, not a new-flow count. With six packets per
colliding flow, a simple no-aging run may report several events for one distinct collider.
Compare this with the separately labelled planned new-flow/hash model, not by treating the
two quantities as interchangeable. Ownership-tag aliases are a finite-fingerprint limit.

The idle window is imported from contracts.py. The analysis counts observed offered flows
inside that window and measures actual per-flow refresh gaps. Configured counts alone do
not prove concurrency. At 100,000 active flows, maintaining one refresh per five seconds
already requires at least 20,000 packets/s. The supplied 50,000 pps profile is a stress
setting, **not a measured safe rate**. C4 may show the BMv2 pipeline cannot sustain it.
Ingress activity does not prove that the software switch processed packets without backlog;
inspect captures, snapshot stability, occupied slots, losses and C4 CPU evidence together.

Snapshot reads require unchanged ownership tags, packet counts and collision counts across
a bracket (§7.4). If drain is insufficient, the cell fails and retains captures rather than
presenting a torn snapshot. Fresh switch/counter state is required at every cell, not reset
while another workload is active. Saturated counters and extra unlabelled ingress traffic
are rejected. Logical bits from the compiled artifact and actual process RSS are separate.

Default input reaches checkpoint 6; checkpoints 16/64 remain unavailable unless explicitly
configured and regenerated. Received-checkpoint accuracy and correct/all-offered fraction
are both reported so packet loss cannot silently improve accuracy. Production memory and
model quality claims require the actual live sweep. No live sweep or Fig. 5 result has been
claimed by this host-only implementation.
