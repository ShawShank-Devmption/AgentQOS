# P2.2 feature-stage register map (review draft)

Scope: design.md §§4.2–4.3, 5.2, 7.1–7.3, 7.9–7.12. This describes the
dataplane state and update order for Person 2's arithmetic review. It does not
change the frozen contract in `common/contracts.py`.

| Register | Index | Purpose |
|---|---|---|
| `reg_flow_key` | flow slot | Independent 32-bit ownership tag |
| `reg_last_ts` | flow slot | Last observed timestamp for ownership and IAT |
| `reg_iat_ewma`, `reg_iat_var_ewma` | flow slot | Saturating timing state |
| `reg_pkt_count` | flow slot | Saturating count and first-eight cursor |
| `reg_bytes_up`, `reg_bytes_down` | flow slot | Saturating directional byte totals |
| `reg_first_sizes` | flow slot × 8 + observation index | First eight packet sizes |
| `reg_flow_class`, `reg_proto_meta` | flow slot | Last class and first-handshake metadata |
| `reg_cm_sketch_0`, `reg_cm_sketch_1` | row × 4096 + column | Active/retired source-flow counts |
| `reg_epoch_flag` | 0 | Selects active sketch |
| `reg_collision_ctr` | 0 | Saturating live-slot collision count (§7.1) |

The register names and array dimensions come from design.md §4 and
`common/contracts.py`. Counter widths, sketch hash seeds, epoch origin, first-size
encoding, and the two division rules remain open in
`docs/feature_arithmetic_design_gaps.md`; no implementation should choose those
values locally.

## Packet update order

1. Admit only complete Ethernet/IPv4/TCP or UDP packets. A non-IPv4 packet,
   noninitial fragment, absent L4 header, or malformed ClientHello forwards as
   UNKNOWN without touching feature state (§7.15).
2. Hash the five-tuple to a 16-bit slot and independently hash it to the 32-bit
   owner tag. Read owner and last timestamp in one `@atomic` ownership/update
   region (§7.2).
3. If the owner differs and the slot is live, increment the collision counter,
   leave **all** slot state unchanged, and mark features invalid. An exact-flow
   override may still classify; otherwise the packet remains UNKNOWN (§7.1).
4. If the owner differs and the slot is stale, reset every per-flow register,
   including all eight size entries, class, and protocol metadata, **before**
   installing the new tag. This is the sole claim path (§7.11). The first
   packet receives count one and no IAT update (§7.12).
5. For an owned slot, update count and directional bytes with saturation.
   Update IAT only when a prior packet exists and timestamp delta is nonzero;
   clamp the 48-bit delta before the fixed-point EWMA operations (§7.3, §7.12).
   Record a size only for observations one through eight.
6. On a successful new-flow claim only, increment each active sketch row for
   the source IP. Read the minimum active-row count as fan-out. The controller
   flips the epoch, waits one sweep, then clears the retired sketch (§7.10).
7. Emit the ten features in the frozen §4.3 order. The switch dump at packet
   counts 6, 16, and 64 is the bit-exact oracle for Person 2's Python extractor
   (§7.9). A live collision never emits a partially updated vector.

## Review gates

- Person 2 approves the arithmetic proposal with numeric 6/16/64-packet
  examples, including zero downstream bytes and saturation boundaries.
- Person 1 compiles each register/control increment with pinned `p4c` and adds
  PTF checks for collision isolation, full reset, first-packet and zero-delta
  IAT, wrap/saturation, and epoch flip.
- Person 2 compares Python extraction with the same PTF register dumps before
  training on the real corpus.

## DESIGN GAP: first observed TLS payload

The parser runs before slot ownership is known, so it cannot itself decide
whether a TCP payload is the first one observed for a flow. Proposed split:
the parser extracts only bounded, provisional ClientHello metadata; ingress
commits `reg_proto_meta` only after a successful new-flow claim and only if
that packet contains a complete eligible ClientHello. If the first observed
packet is a SYN or an incomplete handshake, the exact later-payload policy
needs Person 1/Person 2 agreement before TLS metadata is treated as a tree
feature. The current parser does not yet extract ClientHello fields.
