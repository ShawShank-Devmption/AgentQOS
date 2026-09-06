# Agent-Aware Networking — Requirements (requirements.md)

> **Purpose of this file.** Shared context document for all developers and their LLM assistants.
> Read this first. It explains WHAT we are building and WHY. `design.md` explains HOW.
> `tasks.md` explains WHO does WHAT and WHEN.

---

## 1. Project one-liner

A network switch program (P4₁₆ on BMv2, emulated in Mininet) that recognizes **AI-agent traffic**
— MCP tool-calls, A2A flows, agentic browsers — **per-packet, at line rate, inside the forwarding
pipeline**, and in the same pipeline pass assigns it a **differentiated transport treatment**: its
own queue, scheduler share, DSCP/ECN marking, and admission control. Bursty machine-paced agent
flows get an engineered lane instead of degrading human interactive traffic.

**Course context:** BCSE308L/P Computer Networks · Research Project · 15 weeks.
**Final outcome:** working prototype + Mininet/BMv2 four-baseline evaluation + IEEE-format research
paper targeting publication.

## 2. Why this project exists

- **53% of internet traffic is automated** (Thales 2026 Bad Bot Report): bad bots 40%, benign
  automation 13%, human traffic down to 47%.
- **AI agents are a third traffic category** — they run real browsers with valid TLS fingerprints
  and human-pattern interaction, defeating classic bot detection.
- **AI-driven bot attacks surged 12.5× in 2025** (attack volume, not all agent traffic).
- The concrete harm: an agent fires 50 parallel MCP tool-calls across the same link as a human
  video call. Every switch sees 51 indistinguishable TLS flows on port 443 and queues them
  together. The human's packets wait behind the machine-paced burst — that queuing delay **is**
  the p99 latency spike. **The network cannot fix what it cannot distinguish.**

## 3. Problem statement

Existing QoS machinery (DiffServ, port/app-based classification, endpoint-set DSCP) cannot
recognize AI-agent traffic: agents use real browsers, valid TLS fingerprints, standard ports.
Existing agent-detection (Cloudflare signed agents / Web Bot Auth, DataDome, Akamai) operates only
at the HTTP/CDN edge — it terminates TLS, sees only traffic routed through it, reacts on request
timescales, and offers only allow/block/rate-limit actions.

**No mechanism exists inside the network forwarding path that identifies agent traffic per-packet
at line rate and gives it differentiated transport service.** That coupling — classification AND
QoS action in one data-plane pipeline — is our contribution.

## 4. Novelty (the four axes — every design decision is tested against these)

1. **Location** — forwarding plane, not application edge. Per packet, at line rate, inside the
   switch, no TLS termination, works beyond HTTP.
2. **Action** — transport QoS, not access control. Queueing, scheduling, ECN, admission control.
   An engineered lane, not a verdict. Misclassification degrades service class, never blocks —
   a graceful failure mode no allow/block system offers.
3. **Trigger** — agent/MCP identity specifically, not generic application classes and not
   GPU-cluster AI-workload prioritization.
4. **Features** — agent-protocol/behavioral fingerprints keyed directly to the QoS action in the
   same pipeline.

**Paper contributions (frozen):**
- **C1** — first characterization of agent traffic as a distinct in-network traffic class with
  line-rate-computable features.
- **C2** — a data-plane classifier + QoS coupling architecture with a controller-assisted
  reclassification loop.
- **C3** — reproducible evaluation showing human tail-latency protection under agent bursts
  against four baselines.
- **C4** — adversarial analysis showing timing-based evasion is self-penalizing.

## 5. System flow (end-to-end, plain English)

```
 Agent frameworks (Docker)          Human traffic (trace replay + real browsing)
 Browser Use / AutoGen /            MAWI/CAIDA pcap replay, captured sessions,
 Claude+MCP / Playwright agent      iperf3 background load
        │                                   │
        └──────────────┬────────────────────┘
                       ▼
        ┌───────────────────────────────┐
        │  Mininet topology, BMv2 switch │  ◄── the choke-point link (10–50 Mbps)
        │  running our P4 program        │
        └───────────────────────────────┘
   Per packet inside the switch:
   1. PARSE      Ethernet → IPv4 → TCP/UDP; on flow's 1st packet, TLS ClientHello fields
   2. FEATURES   hash 5-tuple → flow slot; update registers: IAT EWMA mean/var, pkt sizes,
                 byte ratios, per-source fan-out sketch
   3. CLASSIFY   offline-trained decision tree compiled into match-action range tables
                 → 2-bit class label: HUMAN / AGENT-INTERACTIVE / AGENT-BULK / UNKNOWN
   4. ACT        DSCP rewrite, egress queue selection, per-class ECN threshold,
                 per-class meter (admission control), human-protection mode on congestion
                       │
                       ▼ (slow path, first-packet punts + periodic stats)
        ┌───────────────────────────────┐
        │  SDN controller (Ryu, Python)  │  policy install, full JA4, Web Bot Auth verify,
        │                               │  entropy-shift reclassification, state aging
        └───────────────────────────────┘
```

The per-packet decision **always stays in the switch**; the controller only refines it.

## 6. The three traffic classes (not two)

| Class | Meaning | Treatment |
|---|---|---|
| HUMAN_INTERACTIVE | Human browsing, video calls, interactive sessions | Protected queue, priority under congestion |
| AGENT_INTERACTIVE | Latency-sensitive agent tool-calls (MCP request/response) | Good service; bounded share |
| AGENT_BULK | Crawling, batch, scraping | Lowest priority; first to be shaped |

Time-critical agent traffic gets priority treatment too — a stronger QoS story that preempts the
"anti-bot discrimination" objection. Agent traffic is **never rejected**, only isolated.

## 7. Classification features (five families, ranked)

1. **Inter-packet timing (strongest).** Humans have think time (heavy-tailed, high-variance gaps);
   agents are machine-paced (near-deterministic IATs, low coefficient of variation). Computed in
   P4 registers: EWMA IAT mean/variance, min/max IAT, coarse timing entropy. **Evasion is
   self-defeating: an agent inserting human think-time becomes as slow as a human.**
2. **Burst / fan-out structure.** Agents fire N parallel tool-calls with synchronized starts.
   Features: new-flow rate per source (count-min sketch), concurrent flows per source, burst
   length, destination diversity.
3. **Packet-size / flow-shape (survives encryption).** First-N packet sizes, length-distribution
   histogram, up/down byte ratio (agents: structured JSON-RPC up, SSE streams down), flow
   duration. MCP-over-SSE signature: long-lived flow with near-periodic small keepalives.
4. **TLS handshake metadata (JA4 family — useful, never the anchor).** ClientHello is plaintext
   even in TLS 1.3. Python/Go/Node stacks differ from browsers — but Chromium-driving agents are
   browser-identical (~45–60% network-only attribution), and ECH will erode SNI. One tree input,
   limitations stated in the paper.
5. **Explicit/cooperative signals (deployment-dependent).** Web Bot Auth headers (RFC 9421),
   MCP/A2A JSON-RPC signatures — visible only behind TLS termination or on plaintext east–west
   traffic. Classifier runs on families 1–4 everywhere; family 5 is bonus.

## 8. Functional requirements (SRS summary)

| ID | Requirement |
|---|---|
| FR-1 | Parse Eth/IPv4/TCP/UDP on every packet; TLS ClientHello fields on a flow's first packet |
| FR-2 | Maintain per-flow feature state (timing, sizes, byte counters) in data-plane registers |
| FR-3 | Maintain per-source fan-out / new-flow-rate state via count-min sketch |
| FR-4 | Classify every packet into one of 4 labels via decision tree compiled to match-action tables |
| FR-5 | Apply per-class DSCP rewrite, egress queue, ECN marking profile, admission-control meter |
| FR-6 | Human-protection mode: on congestion (queue depth threshold + hysteresis), cap aggregate agent share |
| FR-7 | Punt flow first-packets to controller (rate-limited); controller computes full JA4, verifies Web Bot Auth where visible |
| FR-8 | Controller entropy-shift reclassification loop: re-label flows whose timing drifts; hysteresis against class flapping |
| FR-9 | Flow-state aging: stale slots recycled safely (no feature inheritance across flows) |
| FR-10 | ML pipeline: train sklearn tree (depth ≤ 8) on labeled corpus, mechanically compile to P4 table entries |
| FR-11 | Traffic harness: real agent frameworks in containers vs instrumented MCP target; human trace replay; labeled pcaps |
| FR-12 | Evaluation runner: 4 baselines × agent-share (10–90%) × burst-intensity grid, multiple seeds, CIs |
| FR-13 | Live per-class latency/throughput dashboard for the demo |

## 9. Non-functional requirements & targets

| ID | Requirement / target |
|---|---|
| NFR-1 | **≥30% p99 human-latency reduction** vs best baseline under agent bursts (headline result) |
| NFR-2 | **<5% added per-packet latency** in the data plane |
| NFR-3 | Interactive-agent class must not be starved (report tool-call completion times) |
| NFR-4 | Classification precision/recall per class reported at line rate |
| NFR-5 | State scaling to 100k concurrent flows via sketch approximations; report accuracy vs memory |
| NFR-6 | Fail-open: on any classification failure/unknown, default to best-effort service — never drop, never block |
| NFR-7 | Every experiment reproducible: seeded, scripted, config-driven, one command |
| NFR-8 | Features restricted to what a switch can compute: **nothing requiring payload reassembly** |

## 10. Explicit non-goals (do not build these)

- No blocking, allow/deny, or security enforcement of any kind — QoS only.
- No hardware (Tofino) port — BMv2 emulation only; silicon validation is stated future work.
- No per-framework attribution (which agent product) — class-level discrimination only.
- No IPv6, no QUIC-specific parsing in v1 (note as future work; UDP flows still get timing/size features).
- No cross-domain DSCP guarantees — markings are meaningful within the deploying domain only
  (honest scope note in the paper: DSCP is bleached at AS boundaries).
- No internet-scale deployment story — single-box incremental deployability at a choke point.

## 11. Baselines for evaluation (four)

1. FIFO / best-effort (no differentiation)
2. Standard DiffServ with port/application-based marking
3. Per-flow fair queuing
4. Application-layer rate limiter (edge/WAF-style) — shows in-network reacts faster & covers non-HTTP

**Centerpiece figure:** human p99 latency held flat during an agent storm with our system ON,
versus the spike under every baseline.

## 12. Tech stack (fixed)

| Layer | Technology |
|---|---|
| Data plane | P4₁₆, BMv2 `simple_switch` (v1model) |
| Emulation | Mininet |
| Control plane | Ryu (Python) |
| ML | Python 3.11, scikit-learn |
| Agent traffic | Real agent frameworks in Docker + real MCP server |
| Human traffic | MAWI/CAIDA trace replay (tcpreplay), captured browsing, iperf3 |
| Measurement | tshark, pandas, matplotlib |
| Paper | IEEE LaTeX template; GitHub repo as reproducible artifact |

## 13. Key literature (what devs/LLMs should know exists)

- **Stream A (agent detection, app layer):** arXiv 2606.20910 (97% attribution w/ browser signals),
  2606.30119 (network-layer-only ≈45–60% — the open problem we attack), 2602.09606 (JA4 bot-vs-human
  98.6%), 2510.07176 (AgentPrint, F1 0.866 from timing/volume), RFC 9421 / Web Bot Auth.
- **Stream B (in-network ML):** IIsy, pForest (arXiv 1909.05680), Mousika (INFOCOM'22 — our tree
  compilation recipe), Planter (arXiv 2205.08824), NetBeacon, ACC-Turbo (SIGCOMM'22 — closest
  classification→action precedent, but keyed on DDoS aggregates, not agent identity).
- **Stream C (agents & networks):** NetMCP/SONAR, arXiv 2607.16066 (agents managing networks —
  the inverse of us).

**Gap:** no prior system classifies agentic/MCP/A2A traffic in the forwarding path, and none
couples agent identity to transport-layer QoS. We do both in a single pipeline.

## 14. Team & timeline

- **3 developers**, roles and task split defined in `tasks.md`.
- **15 weeks**, phases 0–5 (lock-in → survey → design → prototype → evaluation → paper+demo).
- All code lives in one GitHub repo, scaffolded from day one; layout defined in `design.md`.

## 15. Vocabulary (so every LLM uses the same words)

| Term | Meaning here |
|---|---|
| Data plane / fast path | The P4 pipeline every packet traverses (ns–µs timescale) |
| Control plane / slow path | The Ryu controller (ms–s timescale) |
| Punt | Cloning a packet to the controller for slow-path processing |
| Flow | 5-tuple (src IP, dst IP, proto, src port, dst port) |
| Flow slot | Register-array index a flow hashes to, holding its feature state |
| Class label | 2-bit value: 0 UNKNOWN, 1 HUMAN_INTERACTIVE, 2 AGENT_INTERACTIVE, 3 AGENT_BULK |
| IAT | Inter-arrival time between consecutive packets of a flow |
| Human-protection mode | Congestion state where agent aggregate share is capped |
| Entropy-shift reclassification | Controller loop re-labeling flows whose timing stats drifted |
| MCP | Model Context Protocol — JSON-RPC tool-call protocol used by AI agents |
| A2A | Agent-to-agent communication protocol |
| JA4 | TLS ClientHello fingerprint family |
