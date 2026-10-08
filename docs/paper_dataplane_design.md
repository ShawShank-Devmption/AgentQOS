# P5.1 draft — Data-plane design and implementation

## Data-plane pipeline

The prototype implements classification and traffic treatment within the P4 forwarding pipeline
on BMv2/v1model. Packets are parsed as Ethernet followed by IPv4 and TCP or UDP. Unsupported
traffic, noninitial fragments, malformed packets, and parsing failures take a fail-open path:
they retain best-effort forwarding and receive the UNKNOWN class. The fast path therefore never
depends on a controller response.

For eligible flows, ingress derives a hash-indexed flow slot and maintains compact state for
timing, packet sizes, byte counts, protocol metadata, and source fan-out. All register
read-modify-write sequences are atomic. Slot ownership prevents feature inheritance: when a
stale occupant is replaced, the pipeline resets every slot register before claim; when an active
collision is detected, the colliding packet is classified as UNKNOWN and is forwarded best effort.
This trades a bounded loss of classification coverage for the avoidance of an incorrect QoS
treatment.

After six packets, the classifier evaluates an offline-trained decision tree represented as
eight match-action levels. A controller-installed exact flow override has precedence; otherwise,
the generated tree tables assign one of HUMAN_INTERACTIVE, AGENT_INTERACTIVE, or AGENT_BULK.
Before warm-up, packets remain UNKNOWN. The controller can refine a classification after
first-packet inspection or a later state sweep, but the switch always retains a complete local
decision path.

## QoS action

The class action table maps the four classes to their configured DSCP codepoints, priority queues,
and meter selection. HUMAN_INTERACTIVE uses the highest-priority queue. AGENT_INTERACTIVE receives
the mid-priority queue and may be ECN-marked but is never dropped by the meter. AGENT_BULK uses
the lowest-priority queue and is the only class eligible for a meter-red drop. UNKNOWN remains
best effort. This keeps classification uncertainty non-disruptive and distinguishes QoS isolation
from access control.

Egress observes dequeue queue depth and maintains a hysteretic congestion flag: it is set at 64
packets and cleared only at 16 packets. While set, the data plane switches to preinstalled,
protective agent-meter presets without a controller round trip. Different ECN thresholds mark
agent traffic earlier than protected human traffic.

## Bounded TLS parsing and punts

The parser performs bounded TLS ClientHello inspection only on TCP payloads that begin with a TLS
handshake record. It extracts a fixed ClientHello prefix and limits extension traversal to sixteen
extensions. An over-bound traversal is recorded as truncated; malformed lengths disable feature
updates. Both cases still forward the packet. First-packet and low-confidence refinement requests
are cloned to the CPU through a globally rate-limited punt path. Lost punts reduce refinement only;
they cannot interrupt forwarding.

## Limitations

BMv2 is a software target, so its absolute latency does not represent switch-ASIC latency.
Accordingly, the evaluation reports relative full-pipeline versus minimal-L2-forwarding overhead
on the same pinned host. The `@atomic` annotations document the required behavior on a concurrent
hardware pipeline. DSCP treatment is meaningful only within the deploying domain, because
inter-domain paths can rewrite or bleach DSCP. The design also does not claim IPv6 or
QUIC-specific parsing in this version. Encrypted ClientHello reduces the usefulness of TLS
metadata, so TLS is an auxiliary input rather than the classifier's anchor.

## Results placeholders

Insert the measured pipeline-overhead CDF, table/register footprint, and state-scaling results
only after the corresponding append-only experiment output is available. All reported numbers must
link to their configuration hash and seed; no result is claimed in this draft.
