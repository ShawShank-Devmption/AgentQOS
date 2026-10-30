# AWS Linux setup and evidence run

The EC2 host runs the emulator; traffic generated on a local shared Wi-Fi network does not pass
through it. Use Ubuntu 22.04 on x86_64 with at least 4 vCPU, 8 GiB RAM, and 40 GiB disk. Restrict
SSH to your own address. The switch, traffic hosts, and target all run inside Mininet on this host.

## 1. Copy the current workspace

The current implementation contains uncommitted files. A GitHub clone alone will not include
them. On EC2, create `~/AgentQOS`; from the development Mac, copy the working tree:

```sh
ssh -i KEY.pem ubuntu@EC2_IP 'mkdir -p ~/AgentQOS'
rsync -az --exclude .git --exclude .venv --exclude .venv-ryu --exclude build \
  -e 'ssh -i KEY.pem' ./ ubuntu@EC2_IP:~/AgentQOS/
```

Run the commands below on EC2 in `~/AgentQOS`.

## 2. Install the pinned Linux stack

Follow `docs/ENVIRONMENT.md` §§2–4. The P4 guide installer may take 1–2 hours. In particular,
rebuild `simple_switch` with `-DSSWITCH_PRIORITY_QUEUEING_ON`; otherwise the queue experiment is
invalid. Create the Python 3.11 `.venv` with the repository's baseline dependencies and install
`pytest` and `ruff`. The implemented controller is currently the standalone `controller.app`
Thrift/AF_PACKET daemon, so Ryu is not required for this smoke run.

```sh
make dev-env
sudo mn --test pingall
make build
make lint
make test
```

If Mininet is installed in Ubuntu's system Python path, ensure the `.venv` can import it before
running the smoke: `.venv/bin/python -c 'import mininet'`. `make build` must compile all three
top-level programs: `l2fwd`, `agent_aware`, and `diffserv`.

## 3. Run the integrated trained-model smoke

The workspace includes a tree trained on **synthetic** profiles at
`results/fanout_fix_20261028/compiled_tree.json`. Use it for the first integrated switch run.
The run exercises policy installation, CPU clone wiring, controller startup, forwarding,
logging, and measurement with the trained policy. The UDP stress workload differs from the
training profiles, so this smoke does not establish accuracy on real agent traffic.

```sh
sudo env AAN_COMPILED_TREE=results/fanout_fix_20261028/compiled_tree.json \
  .venv/bin/python -m eval.run_experiment eval/configs/review_1_smoke.yaml \
  --execute .venv/bin/python -m eval.workload
```

The run creates a new append-only directory under `results/review_1_smoke/`. Keep its
`started.json`, `completed.json`, `config.yaml`, `observations.json`, `summary.json`,
`network/baseline.json`, `network/controller.log`, `network/simple-switch.log`, raw sender and
receiver logs, and `inputs/` (exact profile, model, and compiled pipeline bytes plus SHA-256
hashes). If it fails, inspect the retained `completed.json` and logs and send the first error
plus the relevant switch/controller log excerpt. Do not reuse the output path for a retry;
give the config a new `outputs` path. The `tests/fixtures/smoke_tree.json` UNKNOWN-only policy
is available for a separate plumbing diagnostic, but cannot support classifier claims.

For a packet-level switch check, use `tests/ptf/test_agent_pipeline.py` in a PTF harness with a
fresh `agent_aware.json` BMv2 switch, PTF ports 1, 4, 255, and Thrift port 9090. This checks the
TLS feature register, sketch, clone header, override DSCP, and fragment fail-open behavior. PTF
needs its own switch process and port interfaces, separate from the workload process above.

## 4. Collect paper evidence

The model's training report and held-out data are in `results/fanout_fix_20261028/`. After the
smoke, use the same model for a five-seed Mininet run:

```sh
sudo env AAN_COMPILED_TREE=results/fanout_fix_20261028/compiled_tree.json \
  .venv/bin/python -m eval.run_experiment eval/configs/sanity_ours.yaml \
  --execute .venv/bin/python -m eval.workload
```

Run `sanity_fifo.yaml`, `sanity_diffserv.yaml`, and `sanity_fairq.yaml` with the same
`--execute .venv/bin/python -m eval.workload` suffix. Each run archives its exact inputs under
`inputs/`; retain the complete output directories. The built-in workload uses synthetic UDP
stress. `sanity_app_limiter.yaml` needs an HTTP workload and cannot use this UDP runner. State the
synthetic provenance in the paper. DSCP observations verify emitted treatment, not line-rate
classifier precision/recall. Collect switch verdicts or register evidence for that claim. A model
trained on an independently labeled **real** corpus and real framework traces are still required
for a real-agent claim.

Archive toolchain versions and artifacts with the raw runs:

```sh
p4c --version
simple_switch --version
sha256sum build/*.json
sha256sum p4src/*.p4 controller/*.py eval/*.py harness/*.py
uname -a
```

Report measured effect sizes and confidence intervals only after runs complete. An UNKNOWN-only
smoke, fixture data, or a local network pilot cannot establish the paper's 30% p99 target.
