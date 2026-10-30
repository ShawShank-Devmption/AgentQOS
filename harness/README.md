# Traffic harness

The harness is the only source of training labels. Agent and human runners append orchestration
windows to JSONL; `capture.py` joins those windows to captured five-tuples and writes the frozen
`labels.csv` schema. Labels never come from timing, TLS, packet sizes, or any classifier feature
(design section 7.20).

## Agent runner interface

Each adapter accepts the same required inputs: target URL, task script, source IP, class label,
parallelism, think time, explicit seed, and orchestration-log path. Task scripts are JSON lists of
`{"tool": ..., "arguments": {...}}` objects. The four stable source identifiers are
`browser-use`, `playwright-agent`, `autogen`, and `claude-mcp`.

Each adapter also receives a unique `--result-log` path. Completing the assigned attempt set exits
zero even when the application limiter rejects individual requests; the result file records
completed and failed counts per framework. Configuration, task-script, or result-write failures
still exit nonzero and fail the cell. This distinction keeps limiter behavior measurable instead of
mistaking expected HTTP rejection for harness failure.

The optional `--repetitions` argument repeats the seeded script to sustain an experiment window.
The storm planner derives repetitions from the burst flow rate, agent-share percentage, duration,
and script length. Each simultaneous framework uses its own source IP so orchestration windows
remain unambiguous during label joins.

The checked-in adapters exercise the common MCP wire contract. A real corpus run must execute each
adapter from its named framework's pinned container and archive the image digest, dependency lock,
task script, seed, pcap, orchestration log, and target request log together. Framework credentials
are runtime secrets and must never be written to the repository or capture archive.

## Burst presets

These definitions are the single source used by evaluation configs. Payload profiles refer to the
checked-in target's deterministic task scripts.

| Preset | New flows/s | Parallel connections | Payload profile |
|---|---:|---:|---|
| `low` | 5 | 2 | 80% small compute/search, 20% fetch |
| `med` | 20 | 10 | 50% compute/search, 50% fetch |
| `high` | 50 | 50 | 20% compute/search, 80% fetch |

Every preset still takes an explicit seed. Changing a preset changes experiment semantics and must
be logged in `docs/notebook.md`; do not tune it inside an individual run.

## Human corpus

- Replay MAWI/CAIDA excerpts with `--multiplier=1.0` so original timing is preserved.
- Record the trace identifier, acquisition date, source terms, preprocessing, and packet range.
- [MAWI's archive terms](https://mawi.wide.ad.jp/mawi/) allow WIDE traffic data only for
  research and prohibit privacy-invasive use. The archive states that addresses are anonymized;
  retain the trace identifier and required archive citation with every derivative (checked
  2026-10-06).
- CAIDA passive traces require the applicable request form and
  [Acceptable Use Agreement](https://www.caida.org/about/legal/). The current passive-trace form
  limits access by researcher category, requires the dataset acknowledgment, and requires users to
  report resulting publications. Do not redistribute raw or derived packet data unless the
  specific dataset agreement explicitly permits it (checked 2026-10-06).
- Browsing captures require informed participant consent, at least two hours total, anonymized IPs,
  no payload publication, and a documented retention/deletion date.
- `h_bulk` iperf traffic is background only and is excluded from precision/recall labels.

The repository ignores pcaps and result directories. Store approved corpus objects in the team's
access-controlled LFS/external storage and keep only manifests and hashes in Git.

### Acquired MAWI input

The public samplepoint-B trace `200601011400` is available on this workstation at
`harness/human/corpus/mawi/200601011400.dump` for timing-faithful replay. The ignored local pcap is
466,955,908 bytes with SHA-256
`6d0925f42db3a296ba84976e908f256f14efd39db6ae318f58d8e343bcabf199`. Its committed provenance,
compressed-object digest, provider terms, and preprocessing record are in
`harness/human/manifests/mawi-200601011400.json`.

This establishes one MAWI input, not the complete human corpus. It has not been replayed on the
macOS host because tcpreplay and the Linux Mininet topology are unavailable. CAIDA acquisition
still requires an authorized project member to accept the applicable agreement. Consented browsing
still requires a participant and a recorded retention/deletion date.

### D2 application-shaped traffic profiles

`eval/workload.py`, `harness/demo_traffic.py`, and `harness/traffic.py` add the Dev C D2
profile planner, measured UDP actors, and sender/receiver evidence join. The profile at
`eval/configs/workload_demo.json` describes video/audio, MCP tool calls, ingest uploads and
crawler downloads through the existing `load_profile` API. A constant-size reference profile
is retained at `eval/configs/workload_runtime.json`.

The original Jobin HTTP storm, runtime, experiment completion checks and dashboard remain
the existing execution path. This port does not import Shashank's alternative runtime,
controller, topology, or frozen-contract changes. Its Linux workload adapter cannot be
used unchanged with Jobin's runtime APIs. The D2 planner and measured actors are available
independently; wiring them into the existing audited experiment path and VM validation remain
pending. Do not use the other branch's `--execute ... -m eval.workload` recipe on this branch.

Generate and archive one plan from the frozen experiment config, without running traffic:

```sh
.venv/bin/python -m eval.workload --plan-only \
  --config eval/configs/demo_profile_smoke.yaml \
  --profile eval/configs/workload_demo.json \
  --seed 1 --share 30 --burst low --output results/d2-plan-001
```

The destination must be new. The plan archives the exact selected config/profile bytes and
profile SHA-256; it does not create an experiment completion artifact. Shared class IDs come
from `common/contracts.py`; labels stay in orchestration and are not sent as DSCP or payload
ground truth (§7.20). `harness.traffic` accepts explicit sender/target actor plans, and the
workload's `_run_network_traffic` helper operates on a supplied active Mininet network rather
than assuming a different topology/controller launcher.

Video uses 30-fps frame trains with lognormal frame sizes and 1200-byte payloads spaced
900 µs apart, plus 20-ms audio with 90–159-byte payloads. MCP/bulk sizes, segmentation,
server delays, reasoning gaps, waves, jitter and limits are in the JSON. Independent flows
retain cadence at higher offered loads. Bulk bytes are distributed over recurring wave
intervals; low budgets produce partial transfers, including a shortened final datagram.
The planner validates each class's offered Ethernet-frame bytes (payload + 42 bytes, excluding
FCS/preamble/inter-frame gap) over `duration_s - drain_s` within 2 %, or rejects an infeasible
cell. The packet-rate conversion uses the actual planned mean frame size.

These are one-direction synthetic UDP application patterns, not TCP/TLS/bidirectional
equivalence or real captured MCP agents. Live classification at checkpoints 6/16/64 and QoS
claims require separate Linux evidence. DESIGN GAP: the D2 JSON's low/med/high settings are
experimental, not approved grid presets; the original Jobin storm presets are unchanged.
