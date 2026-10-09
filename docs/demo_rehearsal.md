# Centerpiece demo rehearsal record

The demo claim is valid only after two complete Linux rehearsals. Copy this checklist for each
rehearsal and attach the config hash, seed, logs, pcap hashes, and dashboard recording.

Use `docs/agent_aware_networking_demo.pptx` as the presentation deck. Slides 1–6 establish the
problem, architecture, classes, pipeline, and experiment matrix. On slide 7, switch to the live
dashboard for the OFF/ON comparison. Return to slides 8–10 for the evidence chain, current status,
and readiness gate. The deck deliberately contains no experimental result values until audited
aggregate artifacts and `anchors.json` exist.

Suggested eight-minute pacing:

- 0:00–1:15 — problem and claim-safe contribution (slides 1–2);
- 1:15–3:15 — architecture, classes, and fail-open pipeline (slides 3–5);
- 3:15–4:15 — controlled 375-cell evaluation (slide 6);
- 4:15–6:30 — live OFF/ON scenario and dashboard (slide 7); and
- 6:30–8:00 — evidence chain, status, and rehearsal gate (slides 8–10).

Run from the documented idle Linux VM:

```bash
make dev-env
make build
sudo .venv/bin/python -m eval.run_experiment eval/configs/fifo_sanity.yaml --execute
# After build/agent_aware.json and the Dev B policy lifecycle exist:
sudo .venv/bin/python -m eval.run_experiment eval/configs/review_1_smoke.yaml --execute
```

Open `http://127.0.0.1:8088` while each cell is active. Do not reuse a completed or failed output
directory; clone the sanity config under a new name/seed for the second rehearsal so §7.19 remains
intact.

## Rehearsal 1

- [ ] Date, operator, VM/toolchain commits, CPU affinity, and config hash recorded.
- [ ] `make dev-env`, P4 build, and `make smoke-m1` pass before the demo.
- [ ] MCP target, dashboard, capture, and iperf server report ready.
- [ ] Human flow stabilizes before the seeded high agent burst starts.
- [ ] System-OFF run visibly raises human p99; raw artifacts are archived.
- [ ] System-ON run keeps human p99 flat without starving interactive agents.
- [ ] Per-class throughput and p50/p95/p99 update live and match offline metrics afterward.
- [ ] Cleanup leaves no Mininet, BMv2, tcpdump, iperf, nginx, or dashboard process.
- [ ] Result: **NOT YET RUN**.

## Rehearsal 2

- [ ] Use a fresh seed and a clean VM restart; record the same environment fields.
- [ ] Repeat every readiness, OFF/ON, metric cross-check, archive, and cleanup step above.
- [ ] Confirm speaking cues and recovery steps fit the presentation time limit.
- [ ] Result: **NOT YET RUN**.

## Recovery cues

If live classification or QoS fails, do not substitute fixture numbers. Show the archived successful
rehearsal recording, identify the current failure, and retain the failed run directory. If the
dashboard alone fails, continue the traffic run and regenerate offline metrics from the archived
pcaps/logs. If Mininet/BMv2 fails to start, stop; a host-only simulation is not equivalent evidence.
