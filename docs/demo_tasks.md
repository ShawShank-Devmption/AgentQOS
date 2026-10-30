# Centerpiece demo — remaining tasks (P3.15 / P3.14 / P5.5)

Goal (design.md G4, D6): a human video call and an agent-bulk storm share the 10 Mbps
`choke_v1` bottleneck. With FIFO the call's p99 latency climbs; with our system it stays flat
while AGENT_BULK is metered. The dashboard shows per-class p50/p95/p99 and throughput live.

## Already in place (committed 2026-10-31)

- `agent_aware.p4` compiles with tree tables, override, class actions, M_AI/M_AB meters,
  egress congestion flag with HI/LO hysteresis, and the normal/protective presets (§5.5).
- Controller startup push installs class actions, both meter presets and the compiled tree.
- `eval/configs/demo_call_{ours,fifo,diffserv}.yaml` (80% agent share, high burst, 30 s).
- Synthetic training profiles include `human_video_call`, `agent_crawler`, `agent_ingest`.
  Held-out (synthetic, offline): HUMAN recall 0.91/0.90/1.00 and AGENT_BULK recall
  1.00/0.99/1.00 at 6/16/64 packets (`results/fanout_fix_20261028/model/report.json`).
- Dashboard server/UI polling `/api/metrics` every 2 s.

## Gaps that block the demo

1. No Linux/BMv2 run has happened yet, so nothing below has live evidence.
2. `eval/workload.py` sends constant-rate 1024-byte UDP for every class. That is neither a
   video call nor the flow shape the tree was trained on, so live classification of the demo
   flows is unknown.
3. The dashboard reads `observations.json`/`summary.json`, which the workload only writes when
   the run finishes, so nothing is shown during the run.
4. Meter presets and congestion thresholds have never been tuned on real bursts (P3.14).

## Tasks

| ID | Owner | Task | Depends on | Definition of done |
|---|---|---|---|---|
| D1 | Dev C | Linux VM bring-up per `docs/aws_smoke.md` §2 (Ubuntu 22.04, BMv2 built with `-DSSWITCH_PRIORITY_QUEUEING_ON`). Run the integrated smoke (`review_1_smoke.yaml`) with the trained tree. | — | `make dev-env`, `make build` (3 programs), `make test` green on the VM; PTF `test_parser.py`, `test_agent_pipeline.py`, `test_diffserv.py` pass; smoke run dir with `completed.json` success archived. |
| D2 | Dev C | Demo traffic profile: add a config-driven video-call human flow (frame cadence and sizes matching `harness/synthetic_traffic._human_video_call`) and an agent-bulk storm (matching `_agent_ingest`/`_agent_crawler`) to the workload, as a new profile JSON next to `eval/configs/workload_runtime.json`. Seeded, no hardcoded rates. | — | pytest: deterministic schedule per seed, per-class offered load matches config, packet IDs unique; existing workload tests still pass. |
| D3 | Dev B | Verify the classifier on the demo flows: capture D2 traffic through the switch, compare egress DSCP / `reg_flow_class` against orchestration labels at 6/16/64 packets. If the call is not HUMAN or the storm is not AGENT_BULK, add the D2 shapes to training, retrain, re-run the equivalence gate. Overrides must not be used to force demo labels. | D1, D2 | Per-flow prediction table from a live run (via `eval.pcap_metrics`); equivalence 100%; call → HUMAN and storm → AGENT_BULK by checkpoint 16. |
| D4 | Dev A + Dev B | QoS under congestion (P3.13/P3.14): confirm the congestion flag sets at HI and clears at LO, the protective preset takes effect, AGENT_BULK red packets drop, and HUMAN/AGENT_INTERACTIVE are never dropped by meters (§7.16). Tune `CONGESTION_*_PACKETS` and `METER_PRESETS` for 10 Mbps via a contracts PR if needed. | D1 | PTF/live log showing the flag flip; demo_call_ours human p99 flat (state the target number) versus demo_call_fifo; tuning runs all logged. |
| D5 | Dev C | Live dashboard feed: write a rolling per-class snapshot (p50/p95/p99 latency, throughput) about once a second during the run, replaced atomically, and point `dashboard.server` at it. Add a FIFO-vs-ours view (two sources or two panes). | D2 | pytest for the snapshot writer (partial-window, empty-class = unavailable, never zero); dashboard visibly updates during a 30 s VM run. |
| D6 | Dev C | Optional, makes the call visible: send a real video stream (ffmpeg/GStreamer RTP) from the human host and play it at the receiver, so FIFO shows freezes and ours stays smooth. DESIGN GAP: ffmpeg is a system tool not in the baseline list and needs team sign-off. | D1, D4 | Side-by-side recording of the same seed under FIFO and ours. |
| D7 | Dev C (all rehearse) | Demo runner + runbook: one command runs demo_call_fifo then demo_call_ours (single seed, live variant of the configs), starts the dashboard, archives outputs append-only. Write `docs/demo_runbook.md`. | D3, D4, D5 | Two full rehearsals (P5.5) from the runbook alone; recorded screen capture saved. |

Critical path: D1 → D3/D4 → D7. D2 and D5 can start now on the host.

DESIGN GAP: §10 leaves low/med/high burst presets unspecified; the demo uses `high`. Agree on
the numbers before D2/D4 tuning so demo and evaluation use the same mapping.

## Live gateway: hotspot clients through the switch (cloud design, agreed 2026-10-31)

Goal: devices on the team phone's hotspot reach the internet through BMv2 running
`agent_aware.p4`, and the dashboard on the presenter's Mac shows the classes and QoS applied
to their live traffic. Decided: no extra hardware; gateway on GCP asia-south1; artificial 10 Mbps
cap at the switch; team devices plus walk-ins; Android phone as hotspot.

A phone hotspot sends client traffic straight to cellular, so nothing can sit on that path.
Every device instead runs a WireGuard full-tunnel profile to the VM. Walk-ins install the
WireGuard app and scan a QR code; that opt-in is also their consent. Nothing runs on the phone.

```
devices on hotspot ══ WireGuard UDP 51820 ══► wg0 10.42.0.1/24 (VM)
  veth-lan ══ BMv2 port LAN ─ agent_aware.p4 ─ port WAN ══ veth-wan ── NAT ── internet
  set_queue_rate ~10 Mbps on both ports; controller daemon on the CPU port
  header-only taps on both veths → rolling snapshot → dashboard.server :8080 (localhost)
Mac: gcloud compute ssh VM -- -L 8080:localhost:8080 → http://localhost:8080
```

FIFO vs ours: same VM, swap `l2fwd.json` for `agent_aware.json`; nothing else changes.

Limits to state in the demo and the paper:
- Hairpin via Mumbai adds ~20–50 ms before any QoS effect; measure and show it.
- BMv2 is a software switch and bounds throughput/latency.
- The tree is trained on synthetic profiles; a real video call is UDP/QUIC with no TLS
  ClientHello, so only timing/size features apply. Show predicted classes for walk-ins; claim
  accuracy only for labelled team devices (G6).
- DSCP is meaningful only inside the VM; upstream networks may rewrite it.

| ID | Owner | Task | Depends on | Definition of done |
|---|---|---|---|---|
| G1 | Dev C | GCP VM: `e2-standard-4`, Ubuntu 22.04, asia-south1, static external IP, firewall UDP 51820 + SSH. Toolchain per `docs/ENVIRONMENT.md` (BMv2 with priority queueing). WireGuard server `wg0 10.42.0.1/24`, NAT to the internet, per-device key generation + QR output script. Stop/start scripts; VM stopped when not in use. | — | A phone on the hotspot with the profile browses through the VM (`curl ifconfig.me` shows the VM IP); `make dev-env`, `make build`, `make test` green on the VM; billing alert set. |
| G2 | Dev A | BMv2 inline between `wg0` and NAT as a two-port bump in the wire using veth pairs/netns. Static `tbl_l2_forward` entries for the two inner MACs; broadcast/unknown must not egress the ingress port. `set_queue_rate` + `set_queue_depth` on both ports. Bypass script that bridges the veths directly if `simple_switch` dies. | G1, D1 | Client traffic shows in BMv2 counters; iperf3 through the tunnel capped at the configured rate; killing `simple_switch` restores connectivity in under 5 s. |
| G3 | Dev B | Controller daemon live on the VM: policy push with the compiled tree, CPU port attached, punts from real ClientHellos, sweeps and overrides. | G2 | Log shows punts and per-flow classes for live flows; controller restart mid-traffic re-pushes policy without traffic loss (§7.5). |
| G4 | Dev C | Live dashboard feed: `tcpdump -s 96` on both veths, match with the `eval/pcap_metrics.py` identity, skip ambiguous duplicates (retransmits) instead of failing, class from egress DSCP, rolling per-class p50/p95/p99 + throughput snapshot each second, replaced atomically, served by `dashboard.server` on localhost. Shares the snapshot writer with D5. Mac viewing via SSH tunnel documented. | G2, D5 | Dashboard on the Mac updates live from hotspot traffic; pytest for duplicate handling and empty classes (unavailable, never zero); no payload bytes on disk. |
| G5 | Dev B + Dev C | Demo scenario: team phone on a video call + team laptop running an agent bulk job (`harness/agents` runner or bulk download) and an MCP-tool agent, all through the tunnel. Run under FIFO and under ours on the same VM. | G3, G4, D4 | Recorded side-by-side runs: call p99 through the gateway under FIFO vs ours; bulk throughput metered; hairpin latency reported. |
| G6 | Dev B | Accuracy on real traffic from labelled team devices (orchestration labels). Feed captures into the real corpus (P3.4/P3.8); retrain + equivalence gate if the call or the bulk job is misclassified. | G3 | Per-flow prediction table for labelled live flows; retrain documented if needed. |
| G7 | All | Consent and data handling: QR card states what is captured; header-only captures; walk-in data deleted after the demo; VM wiped of captures before shutdown. | — | Written policy in the runbook, agreed by all three devs. |

Critical path: G1 → G2 → G3/G4 → G5. G1 and G7 start now. G2 waits on the port contract below.

DESIGN GAP (contract change, team decision before G2): `common/contracts.py` fixes
`SENDER_SWITCH_PORTS = (1, 2, 3)` and `TARGET_SWITCH_PORT = 4`, and rate caps/congestion are
configured only on port 4 (server-facing, i.e. upload for clients). On the gateway the congested
direction is download (toward `wg0`, the LAN port). A `live_gateway` topology with an agreed
LAN/WAN port map and congestion handling in both directions needs a contracts PR. Without it,
mapping LAN = port 1, WAN = port 4 protects only upload traffic.
