# Dev C literature and data-source audit (P1.3)

Checked against primary sources on 2026-10-09. This note records only claims needed by the
evaluation and related-work sections. It is not a substitute for reading the cited sources.

## Agent/network systems

| Key | Primary source | Supported use in this project | Boundary |
|---|---|---|---|
| NetMCP | E. Li, H. Du, and K. Huang, “NetMCP: Network-Aware Model Context Protocol Platform for LLM Capability Extension,” arXiv:2510.13467, 2025. [Abstract](https://arxiv.org/abs/2510.13467) | NetMCP supplies a heterogeneous MCP test platform. Its SONAR algorithm combines semantic similarity with observed network/server QoS to select tools. It is the closest Stream C comparison for network-aware MCP operation. | SONAR routes at the MCP application layer. It does not identify agent traffic in a forwarding pipeline or apply class-specific in-network QoS. |
| Agentic-network survey | M. Ameur, A. Mekrache, B. Brik, and A. Ksentini, “LLM-Powered Agentic AI for 5G/6G Networks: A Tutorial and Survey on Architectures, Protocols, and Standardization,” arXiv:2607.16066v1, 2026. [Abstract and version record](https://arxiv.org/abs/2607.16066v1) | The survey maps agent capabilities to 5G/6G control, management, and AI-native planes and documents agents *managing networks*. This supports describing that direction as the inverse of the present system. | The source is a July 2026 preprint marked “under review”; do not describe it as peer reviewed or as an implementation of data-plane agent classification. |

The defensible gap statement is narrow: these verified Stream C sources do not present a system
that infers agent classes from forwarding-path traffic and couples those classes to in-network
queueing/metering. A final novelty claim still requires a submission-date literature rescan.

## DiffServ, congestion signalling, and queueing

| Key | Primary source | Supported use in this project |
|---|---|---|
| RFC 2474 | K. Nichols et al., “Definition of the Differentiated Services Field,” 1998. [RFC Editor](https://www.rfc-editor.org/info/rfc2474) | The six-bit DSCP selects a per-hop behavior. Classification, marking, and conditioning may occur at a domain boundary. This is the standards basis for the static-DiffServ baseline and DSCP actions. |
| RFC 2475 | S. Blake et al., “An Architecture for Differentiated Services,” 1998. [RFC Editor](https://www.rfc-editor.org/info/rfc2475) | DiffServ obtains scalability by aggregating traffic and combines classifiers with marking, metering, policing, shaping, scheduling, and queue management. |
| RFC 2597 | J. Heinanen et al., “Assured Forwarding PHB Group,” 1999. [RFC Editor](https://www.rfc-editor.org/info/rfc2597) | Assured Forwarding defines independently forwarded classes with drop precedence; it is useful context for class isolation, not evidence that this project implements AF exactly. |
| RFC 3246 | B. Davie et al., “An Expedited Forwarding PHB,” 2002. [RFC Editor](https://www.rfc-editor.org/info/rfc3246) | Expedited Forwarding motivates a low-loss, low-delay forwarding treatment under a configured rate. The project must not claim RFC 3246 conformance without its required rate behavior. |
| RFC 3168 | K. Ramakrishnan, S. Floyd, and D. Black, “The Addition of Explicit Congestion Notification (ECN) to IP,” 2001. [RFC Editor](https://www.rfc-editor.org/info/rfc3168) | ECN-capable transports can receive CE marking instead of loss as congestion notification. This supports the ECN action, subject to the packet being ECN capable. |
| Fair queueing | A. Demers, S. Keshav, and S. Shenker, “Analysis and Simulation of a Fair Queueing Algorithm,” SIGCOMM '89, pp. 1–12. [Author-hosted paper](https://people.eecs.berkeley.edu/~sylvia/papers/FQ1989.pdf), [DOI](https://doi.org/10.1145/75246.75248) | Fair queueing was evaluated as an alternative to FCFS that isolates ill-behaved sources and reduces delay for sources below their share. The repository baseline is only a per-flow hashed round-robin approximation, not this exact algorithm. |
| RED | S. Floyd and V. Jacobson, “Random Early Detection Gateways for Congestion Avoidance,” IEEE/ACM Transactions on Networking, 1(4), 1993. [Author archive](https://fathom.icsi.berkeley.edu/floyd/papers/red/red.html), [DOI](https://doi.org/10.1109/90.251892) | RED detects incipient congestion from average queue size and probabilistically drops or marks. It is historical context for early congestion response, not a claim that BMv2 meters are RED. |
| FQ-CoDel | T. Hoeiland-Joergensen et al., “The Flow Queue CoDel Packet Scheduler and Active Queue Management Algorithm,” RFC 8290, 2018. [RFC Editor](https://www.rfc-editor.org/info/rfc8290) | FQ-CoDel combines flow hashing/isolation with active queue management. It motivates the fair-queueing comparison, but the configured baseline must be named according to its actual hashed scheduler. |

Citation correction: the canonical SIGCOMM fair-queueing DOI contains `75246`, not `75247`.

## Trace access and use

| Source | Verified access and terms | Repository status |
|---|---|---|
| MAWI samplepoint-F/B | The [MAWI archive](https://mawi.wide.ad.jp/mawi/) publishes WIDE backbone packet traces, states that IP addresses are scrambled, restricts the data to research use, and prohibits privacy-invasive activity. Its [FAQ](https://mawi.wide.ad.jp/mawi/faq.html) describes prefix-preserving anonymization, timestamp limitations, asymmetric visibility, packet loss during collection, and the required archive citation. | Public trace `200601011400` from samplepoint-B is the bounded acquisition candidate. Its source page reports a 15-minute trace and 184.23 MB compressed. The ignored local object and committed SHA-256 manifest are corpus inputs, not redistributable repository content. |
| CAIDA passive traces | CAIDA's [passive-data access page](https://www.caida.org/catalog/datasets/passive_dataset_download/) restricts downloads to authorized users. The [Acceptable Use Agreement](https://www.caida.org/about/legal/aua/) prohibits de-anonymization and unauthorized redistribution, and requires collaborators to obtain their own access. The [request form](https://www.caida.org/catalog/datasets/request_user_info_forms/passive_sampler_dataset_request/) requires dataset acknowledgment and publication reporting. | Not downloaded: the repository has no authorized CAIDA identity or accepted dataset-specific agreement. This is an external access prerequisite, not an implementation failure. Never substitute scraped credentials or another researcher's copy. |
| Consented browsing | Project protocol in `harness/README.md`: informed consent, anonymized addresses, no published payloads, and a recorded retention/deletion date. | No participant capture is present. Collection requires a consenting participant and lab session; synthetic traffic cannot be reported as this corpus. |

## Acquisition record requirements

Every externally stored trace or browsing capture must retain:

- provider and provider trace identifier;
- canonical source and usage-terms URLs;
- acquisition timestamp, byte size, and SHA-256 of the stored object;
- compression and preprocessing commands, packet/time range, and replay multiplier;
- anonymization status and any dataset-specific acknowledgment;
- access-control location, permitted users, redistribution rule, and retention/deletion date.

Raw traces remain outside Git. A manifest proves provenance and integrity; it does not grant
permission to redistribute the trace or establish that it was successfully replayed.

## Claims deliberately excluded

- “All traces downloaded”: false until an authorized CAIDA user obtains the selected dataset.
- “Four real agent frameworks captured”: the dependency-free adapters exercise the wire contract
  but do not establish framework provenance.
- “End-to-end latency from both taps”: current live telemetry reports TCP ACK RTT from one tap.
  Design section 10's embedded-timestamp/two-tap measurement remains an evaluation gap.
- “No prior work”: too broad. Use the bounded, source-qualified gap statement above and repeat the
  literature scan immediately before submission.
