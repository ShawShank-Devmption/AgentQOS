# Browser-agent diagnosis — 2026-10-28

P4.6 offline research diagnostics, consuming the unchanged design §4.3 ten-feature vector and
§4.4 orchestration labels. This implements the reporting part of §8/§11; live classification
at line rate remains a Linux/BMv2 evaluation gate. No classifier, label, TLS fixture, traffic
profile, P4 export, or QoS treatment is changed by this analysis.

The browser weakness persists after correcting checkpoint-specific fan-out extraction. It is
not evidence that all agent traffic is undetectable: the MCP proxy remains substantially easier.
The synthetic browser model shares the transport behavior and Chromium ClientHello with human
browsing. Most human/agent session differences happen between separate flows, beyond most
per-flow checkpoints. Increasing model capacity or removing features increases browser recall
at a substantial human false-positive cost in these measurements.

## Evidence and methodology

Canonical evidence is `results/browser_diagnosis_20261028/run/report.json`; the explicit
orchestration mapping is `results/browser_diagnosis_20261028/groups.csv`. Inputs are
`results/fanout_fix_20261028/features.csv` and the versioned local diagnostic settings in
`eval/configs/browser_diagnosis.json`. The report records SHA-256 hashes of all three inputs.
Results directories are intentionally ignored by Git and can be regenerated below.

The corpus has 11,393 checkpoint rows, 4,094 flows and 240 independently generated client
sessions (40 per synthetic profile, generator seed 1). These are modeled workloads, not real
Browser Use/Playwright/MCP captures and not evidence of the project's real-corpus or line-rate
acceptance criteria. Browser-only counts are 545 human-browsing rows and 867 agent-browser rows.

The fixed analysis uses seeds 1, 2, 3, five CV folds, a 20% session-held-out split, balanced depth-8
trees, four predeclared feature sets, and a 100-tree random forest as a diagnostic of model
capacity. The forest is never exported, selected for deployment, or represented as a P4 tree.
The diagnostic trees do not assert the production §7.14 path-coverage gate; ablations are
measurements, not deployment candidates.

For each seed, reserve complete sessions first. Use only the remaining development rows for
feature ablations and model-capacity comparisons. Within that same development subset, compare
CV grouped by flow with CV grouped by actual orchestration session. Every split reports shared
flow/session counts. Every flow-grouped fold is flow-disjoint but shares sessions; every
session-grouped fold and reserved split has zero shared sessions and flows. Flow CV is an
intentional diagnostic of optimistic evaluation, not an acceptable independence claim.

Only the predeclared full depth-8 tree is evaluated on reserved rows. No winner is chosen from
reserved performance. This is a retrospective diagnostic of previously inspected proxy data;
reserved rows are not a new, untouched research test set. Separate capture sessions and a new
locked evaluation corpus are required for final generalization claims. The three seeds reuse
one corpus, so their ranges are split sensitivity, not independent-corpus confidence intervals.

The CLI requires an exact `flow_id,group_id` sidecar from orchestration. It never derives session
membership from flow-ID text, source labels or framework names. Label, source, flow ID, group ID
and checkpoint metadata never become model inputs. All models consume explicit subsets of
`common.contracts.FEATURE_ORDER` only. The report includes per-class precision/recall, confusion
matrices with fixed label order, agent-as-human errors, checkpoint counts and profile counts.
Absent subsets have zero support and null undefined rates. Aggregate macro-F1 uses all three
frozen labels; profile-specific precision is conditional on that profile, so use recall and
confusion counts to assess its errors.

## Results

The following are arithmetic means over the three development CV runs, combining each run's
out-of-fold checkpoint predictions. They are row-weighted within a run: longer flows contribute
more reached checkpoints. Checkpoint-specific reports are available separately in JSON.

| CV grouping | Feature set / model | Browser-agent recall | Human-browsing recall | MCP-agent recall | Overall macro-F1 |
|---|---|---:|---:|---:|---:|
| Flow | Full depth-8 tree | 37.89% | 86.38% | 98.88% | 0.8066 |
| Session | Full depth-8 tree | 35.85% | 77.12% | 97.11% | 0.7538 |
| Session | Tree without TLS | 34.10% | 74.95% | 95.19% | 0.7336 |
| Session | Tree without fan-out | 41.43% | 76.02% | 67.80% | 0.7309 |
| Session | Timing, fan-out, age tree | 60.96% | 48.92% | 62.29% | 0.6720 |
| Flow | Full diagnostic forest | 79.96% | 65.76% | 98.88% | 0.8674 |
| Session | Full diagnostic forest | 65.44% | 46.09% | 98.96% | 0.7808 |

The forest's browser recall falls by 14.52 percentage points when sessions are separated, and
its human-browsing recall is only 46.09% under session CV. Capacity is not a demonstrated fix.
Removing fan-out sacrifices MCP recall; using timing/fan-out alone trades many human errors for
browser recall. None of these comparisons justifies changing the frozen feature vector or
claiming universal browser-agent discrimination.

The reserved full-tree results also vary sharply with session split:

| Seed | Held-out sessions | Browser rows | Browser recall | Human-browsing recall | MCP recall |
|---|---:|---:|---:|---:|---:|
| 1 | 48 | 253 | 36.76% | 59.78% | 100.00% |
| 2 | 48 | 194 | 0.52% | 98.89% | 97.83% |
| 3 | 48 | 148 | 33.78% | 74.65% | 100.00% |

Session-CV full-tree browser recall at checkpoints 6/16/64 ranges respectively from
46.46–52.34%, 12.96–47.12%, and 2.08–47.69%. Only 48–65 development browser rows per run reach
checkpoint 64; the population changes with checkpoint and those small subsets should not be
interpreted as a causal effect of observing more packets. Do not tune using the reserved rows.

## What the observations explain

`harness/synthetic_traffic.py::_human_browsing` and `_agent_browser` both call `_browse` using the
same Chromium hello and object/connection generation. They differ in page count and page-gap
sampling. `_browse` creates new connections for each page, applying the page gap after the
page's flows finish. A packet's own flow history therefore usually does not contain that
between-page think time. The source fan-out sketch retains only current-epoch new-flow counts;
it is not a session history or client identity signal. The human minimum inter-page gap and
assumed LLM-step distribution also overlap. These are generator assumptions, not measured human
or browser-agent behavioral distributions.

In this corpus both browser profiles have exactly `(tls_ext_count, tls_alpn_class) = (7, 0)`.
At checkpoint 6 both have median fan-out 2. At checkpoint 64 their median IAT EWMAs are 467 and
471.5 microseconds. Similar medians do not prove identical distributions, but they corroborate
why TLS and early packet transport cannot be treated as browser-agent identity here. MCP's
Python HTTP client and tool-call shape are a different, easier proxy classification problem.

Exact opposing-label ten-feature vectors were **not observed** in the browser subset at any
checkpoint: 683, 605 and 124 browser rows at 6, 16 and 64 packets, respectively. The report's
observed minimum deterministic-fit errors are therefore zero. This is only an empirical
memorization constraint: continuous/noisy timing can yield unique vectors even when the class
distributions overlap heavily. It is neither a population Bayes-error bound nor evidence of
separability. A hand-built regression with identical opposite-label vectors independently
checks that observed contradictory labels correctly impose a 50% empirical accuracy ceiling.

DESIGN GAP: the frozen per-flow feature schema has no cross-flow session timing history or
cooperative browser identity. This analysis does not establish that such additions are needed
or sufficient. Collect real independently grouped human and agent browser sessions on shared
sites, task mixes, browsers and network conditions before proposing any contract change. Keep
existing labels and fail-open behavior while reporting the browser uncertainty explicitly.

## Reproduction

Create a fresh evidence directory. Generate features and the sidecar together from explicit
orchestration objects; no generic inference from ID naming is used:

```sh
.venv/bin/python - <<'PY'
from pathlib import Path
import pandas as pd
from harness.synthetic_traffic import corpus_frame, generate_flows

out = Path("results/browser_diagnosis_reproduction")
out.mkdir(parents=True, exist_ok=False)
flows = generate_flows(seed=1, clients_per_profile=40)
corpus_frame(flows).to_csv(out / "features.csv", index=False)
pd.DataFrame(
    [(flow.flow_id, flow.client_id) for flow in flows],
    columns=["flow_id", "group_id"],
).to_csv(out / "groups.csv", index=False)
PY
.venv/bin/python -m ml.diagnose_browser \
  --features results/browser_diagnosis_reproduction/features.csv \
  --groups results/browser_diagnosis_reproduction/groups.csv \
  --config eval/configs/browser_diagnosis.json \
  --out results/browser_diagnosis_reproduction/run
.venv/bin/pytest tests/test_diagnose_browser.py
make lint
```

The local JSON config is intentionally separate from the frozen §4.6 experiment YAML schema.
Outputs must be new directories; reruns cannot overwrite existing evidence. There are no model
pickle files or compiled P4 artifacts in diagnostic output.
