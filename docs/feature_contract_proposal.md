# P2.2/P2.4 feature contract proposal — awaiting team approval

This is a review draft for Persons 1 and 2. It proposes values missing from
design.md §4.3 and the behavior needed for bit-exact P4/Python extraction.
It does **not** amend the frozen contract. A PR changing design.md §4 and
`common/contracts.py` needs two approvals before implementation uses these
choices.

**Blocking contract omission:** `flow_age_ms` requires the first-observed
timestamp, but §4.2 lists only `reg_last_ts`, which is overwritten on every
packet. The example below assumes `first_seen_us` exists. Person 1 and
Person 2 must propose a dedicated first-timestamp register (or another
bit-exact storage layout) in the same two-approval contract amendment.

## Proposed arithmetic

All packet sizes and byte counters refer to the Ethernet frame length observed
at ingress. `pkt_count` counts both directions; `bytes_up` counts packets
entering from sender-side topology ports, and `bytes_down` counts packets
entering from the target-side port. A flow with no downstream observation is
therefore explicitly asymmetric, rather than being discarded (§7.14).

Define `ceil_log2(x)` as zero for `x <= 1`, otherwise the bit length of
`x - 1`. For every expression below, intermediate additions and shifts use
enough width to avoid wraparound before the documented saturation step.

| Feature | Proposed integer rule |
|---|---|
| `mean_pkt_size_up` | If count is zero, 0; otherwise saturate to uint16 after `bytes_up >> ceil_log2(pkt_count)`. |
| `updown_ratio_x100` | If `bytes_down == 0`, 65535 when `bytes_up > 0`, else 0. Otherwise saturate to uint16 after `((bytes_up << 6) + (bytes_up << 5) + (bytes_up << 2)) >> ceil_log2(bytes_down)`. Form the numerator in uint64. |
| `flow_age_ms` | Saturate to uint32 after `(now_us - first_seen_us) >> 10`. This approximates milliseconds using 1024 µs per unit; the frozen feature name and unit need to document that approximation. |
| IAT mean | Start at zero. For each nonfirst packet with nonzero delta, update `mean += (delta - mean) >> 3` with a signed arithmetic shift and saturate to uint32. |
| IAT variance | After updating mean, update `variance += (abs(delta - mean) - variance) >> 3` with the same signed-shift rounding; saturate to uint32. |

The denominator choice uses the next power of two, so the two approximate
quotients never exceed their exact nonzero-denominator counterparts. It is a
deliberate downward bias shared by training and serving, not a claim of exact
division. The shift-only `flow_age_ms` rule is about 2.4% low relative to
exact milliseconds. These biases should be reported in the feature methodology.

## Proposed discrete encodings

- `first8_size_bucket`: one bit per observed packet, first packet in bit 7,
  eighth in bit 0. Set the bit when Ethernet frame length is at least 512
  bytes; unobserved positions remain zero. The existing eight size registers
  retain the raw first-eight values.
- `tls_alpn_class`: 0 = none, 1 = h1 (`http/1.1`), 2 = h2 (`h2`), 3 = other.
  Classify the first complete protocol token in the ALPN extension. If the
  extension is absent, use none; malformed length fields fail open with no
  protocol metadata committed.
- SNI length bucket: 0 = absent, 1 = 1–32 bytes, 2 = 33–128 bytes,
  3 = 129 or more bytes. The parser also retains the exact first-hostname
  length provisionally, but the proposed register stores only the bucket.
- Count-min sketch: two epochs, each four rows × 4096 columns. For row `r`
  in 0..3, hash the 40-bit concatenation `(src_ip, uint8(r))` with standard
  CRC32 and use the low 12 bits as the column. Each counter is uint16 and
  saturates at 65535. The row byte acts as the four explicit hash salts.
  Read the minimum active-row count. Epoch zero starts when the switch state
  is reset for a replay; the controller flips the active epoch every 1 s and
  clears a retired epoch only after one sweep (§7.10). Capture/replay must
  record the reset and flip times so offline extraction can use identical
  windows.

## Proposed `reg_proto_meta` layout and commit rule

Use one 32-bit slot entry with bits 0–7 `tls_ext_count`, 8–15
`tls_alpn_class`, 16–23 `tls_cipher_count`, 24–25 SNI length bucket,
26 `truncated`, 27 `payload_seen`, 28 `tls_present`, and 29–31 reserved
zero. These bit positions are a **proposal**, not the current frozen
contract. The parser exposes semantic one-hot ALPN flags until the numeric
encoding is approved.

The ingress stage resets this entry on every stale-slot claim (§7.11). A
SYN does not set `payload_seen`. On the first packet with
`tcp_payload_present=1` and `feature_valid=1`, ingress sets `payload_seen`;
if `tls_record_candidate=1`, it also commits the TLS fields. An ordinary
non-TLS payload sets only `payload_seen`. A malformed or incomplete packet
has `feature_valid=0` and performs no register write (§7.15), so a later
valid payload can still supply the first eligible observation. This is the
proposed resolution to the parser-before-flow-state ordering gap.

## Worked checkpoint example

One flow has packets exactly 1024 µs apart, alternating 200-byte upstream
and 100-byte downstream Ethernet frames, starting upstream at `t=0`.
There is one new flow from this source in the active epoch, no TLS
ClientHello, and no sketch collision. The table uses the rules above and
the §4.3 feature order.

| Packets | IAT mean | IAT variance | Count | Mean up size | Up/down ×100 | First8 bits | Fan-out | TLS ext | ALPN | Age units |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 6 | 498 | 328 | 6 | 75 | 117 | 0 | 1 | 0 | 0 | 5 |
| 16 | 883 | 257 | 16 | 100 | 156 | 0 | 1 | 0 | 0 | 15 |
| 64 | 1017 | 7 | 64 | 100 | 156 | 0 | 1 | 0 | 0 | 63 |

At six packets, for example, `bytes_up=600`, `bytes_down=300`, so mean
size is `600 >> 3 = 75` and ratio is `60000 >> 9 = 117`. The exact
physical ratio would be 200; the tree must train on 117. The timing values
assume signed right shift rounds negative values toward negative infinity.

## Review questions before contract amendment

1. Does the team accept the downward bias from next-power-of-two division,
   especially for the ratio feature? If not, choose a different P4-feasible
   shift rule and regenerate the checkpoint table.
2. Does the 512-byte first-eight bit signature preserve enough information
   on the real corpus? Check this before training, without changing labels.
3. Can the deployed BMv2 build expose the same CRC32 behavior to the Python
   extractor, and can the replay harness record epoch boundaries exactly?
4. Is the first advertised ALPN token the right class choice for multi-token
   lists? The bounded parser must cap extension traversal at 16 and forward
   malformed or truncated handshakes as UNKNOWN (§7.15).
5. Approve the first-timestamp storage needed for `flow_age_ms`; no current
   register in the frozen list can reproduce it after `reg_last_ts` changes.
6. Approve or revise the `reg_proto_meta` bit layout and the first-eligible-
   payload rule above before P3.1 writes this register or Person 2 reads it.
