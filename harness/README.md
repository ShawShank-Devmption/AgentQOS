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
