# Centerpiece demo rehearsal record

The demo claim is valid only after two complete Linux rehearsals. Copy this checklist for each
rehearsal and attach the config hash, seed, logs, pcap hashes, and dashboard recording.

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
