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
