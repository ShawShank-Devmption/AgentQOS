# Feature Arithmetic Specification (task P2.4)

**Status:** proposed by Dev B. It is approved when Dev A's PTF replay of
`tests/fixtures/feature_worked_examples.json` through `p4src/features.p4` reproduces every expected
register and feature value exactly.

This document defines, bit for bit, how the frozen feature vector of design.md §4.3 is computed. The
P4 pipeline (`p4src/features.p4`, Dev A) and the Python mirror (`common/feature_math.py` and
`ml/extract_features.py`, Dev B) both implement it. Constants come from `common/contracts.py`.
Approximations are listed in §7 so the paper can state them.

## 1. Per-packet order of operations

For a packet whose slot is owned by its flow (design.md §5.2 step 2):

1. **Claim.** If `reg_pkt_count[slot] == 0`, set `reg_first_ts := now`. `now` is
   `standard_metadata.ingress_global_timestamp` (48-bit µs).
2. **Gap.** Otherwise `Δ = (now − reg_last_ts) mod 2^48`, clamped to `2^32 − 1` (§7.3).
   - If `Δ == 0`, skip the IAT update (same-timestamp burst, §7.12).
   - Else if `reg_iat_ewma == 0`, set `reg_iat_ewma := Δ`. The first non-zero gap seeds the mean
     and `reg_iat_var_ewma` stays 0.
   - Otherwise run the EWMA update of §2 (features 0 and 1).
3. `reg_last_ts := now`.
4. If `reg_pkt_count < 8`, store the packet size in `reg_first_sizes[slot·8 + reg_pkt_count]`.
5. `reg_pkt_count := sat32(reg_pkt_count + 1)`.
6. Direction comes from the ingress port: upstream if the packet arrives on a sender-host port
   (agents, human, bulk), downstream if it arrives from the target. Add the size to `reg_bytes_up`
   or `reg_bytes_down`, saturating at 32 bits.
7. On the flow's **first payload-carrying packet only** (either direction): if the TCP payload
   starts with `0x16 0x03`, parse the ClientHello (§4) and write `reg_proto_meta`. Any other
   first payload leaves `reg_proto_meta = 0`. Later payloads are never parsed.
   *Open (G18):* P4 needs a per-slot "payload seen" bit for this rule; see
   `docs/feature_arithmetic_design_gaps.md`.

**Packet size** is the frame length, `standard_metadata.packet_length`. Offline this is the pcap
original length.

The classifier reads the feature vector after these updates, so features at checkpoint *k* are
the values after the *k*-th packet is processed.

## 2. Features

All values are unsigned. `satN(x) = min(x, 2^N − 1)`. `floor_log2(x)` is the index of the highest
set bit; in P4 this is a ternary/LPM table or an if-chain over the bits.

| # | Feature | Definition | Width | Zero / edge value |
|---|---|---|---|---|
| 0 | `iat_ewma_us` | `ewma_step(ewma, Δ)` | 32 | 0 until the first non-zero gap; then seeded with Δ |
| 1 | `iat_var_ewma` | `ewma_step(var, abs(Δ − ewma_old))` | 32 | 0 |
| 2 | `pkt_count` | packets in both directions | 32 | saturates |
| 3 | `mean_pkt_size_up` | `sat16(bytes_up >> floor_log2(pkt_count))` | 16 | 0 when `pkt_count == 0` |
| 4 | `updown_ratio_x100` | `sat16((bytes_up·100) >> floor_log2(bytes_down))` | 16 | 65535 when `bytes_down == 0` |
| 5 | `first8_size_bucket` | bit (7−i) set when packet i ≥ 128 bytes | 8 | unseen packets read 0 |
| 6 | `fanout_new_flows` | min over 4 rows of the active-epoch sketch for the client IP | 16 | — |
| 7 | `tls_ext_count` | `reg_proto_meta >> 8` | 8 | 0 |
| 8 | `tls_alpn_class` | `reg_proto_meta & 0xFF`: NONE=0, H1=1, H2=2, OTHER=3 | 8 | 0 |
| 9 | `flow_age_ms` | `Δ(now, reg_first_ts) >> 10` (1.024 ms units) | 32 | — |

**EWMA step** (EWMA_SHIFT = 3). Each register is updated with:

```
if sample >= current: current = current + ((sample - current) >> 3)
else:                 current = current - ((current - sample) >> 3)
```

The magnitude is shifted before the sign is applied, so the step truncates toward zero. A Python
`(sample - current) >> 3` would floor on negative values and differ by one: `ewma_step(10, 3)` is
10, not 9. `ewma_old` in feature 1 is the mean **before** this packet's update, so P4 reads each
register once.

**×100 in P4:** `(x << 6) + (x << 5) + (x << 2)`, computed in a 64-bit temporary before the shift.

## 3. Hashing

**Canonical flow key** (104 bits, client side first): `client_ip:32, server_ip:32, proto:8,
client_port:16, server_port:16`. P4 picks the client side by ingress port (upstream packets as-is,
downstream packets with addresses and ports swapped). Offline, the flow initiator is the client;
the two agree under `choke_v1`.

| Value | BMv2 call | Python mirror |
|---|---|---|
| slot | `hash(slot, crc32, 0, {key, 8w0}, 32w65536)` | `zlib.crc32(key + b"\x00") % 65536` |
| tag (`reg_flow_key`) | `hash(tag, crc32, 0, {key, 8w1}, 2^32)` | `zlib.crc32(key + b"\x01")` |
| sketch column, row r | `hash(col, crc32, 0, {client_ip, 8w r}, 32w4096)` | `zlib.crc32(ip + bytes([r])) % 4096` |

Sketch counters are 16-bit and saturating. A flow claim increments all four rows of the active
epoch for the client IP. The controller flips `reg_epoch_flag` every second and zeroes the retired
sketch one tick later (design.md §6.4, §7.10).

**Open check (P3.3):** that BMv2's `crc32` equals `zlib.crc32` (reflected, init and xorout
`0xFFFFFFFF`). The fixture's `slot` and `tag` values settle it on the first PTF run.

## 4. ClientHello bounds (features 7–8)

The parse looks at the first payload segment only:

- record header (5) → handshake type must be `0x01` → version (2) + random (32)
- → session id, cipher suites, compression methods (each length-prefixed)
- → extensions block length (2).

Extensions are counted while the 4-byte header **and** the body both lie inside the segment and
inside the declared extensions block, up to 16 (`TLS_MAX_EXTENSIONS`). An ALPN extension (`0x0010`)
sets the class from its first protocol name: `http/1.1` → H1, `h2` → H2, anything else or
unreadable → OTHER. If anything runs out before the extensions block, the result is `(0, NONE)`,
which is the fail-open value (design.md §7.15). This covers non-TLS payloads, a handshake type
other than ClientHello, and truncated headers. `reg_proto_meta = (ext_count << 8) | alpn_class`.

## 5. Worked examples

All scenarios use the flow `10.0.0.1:40000 → 10.0.0.100:443/TCP`, which gives slot `59606` and
tag `2190923840`, with `fanout_new_flows = 1`. The full packet lists and register snapshots are in
`tests/fixtures/feature_worked_examples.json`, which is the file Dev A's PTF test replays.

| Scenario | Pkts | Vector (order of design.md §4.3) |
|---|---|---|
| `agent_constant_pacing` | 6 | 2000, 0, 6, 300, 58, 252, 1, 0, 0, 9 |
| | 16 | 2000, 0, 16, 200, 39, 255, 1, 0, 0, 29 |
| | 64 | 2000, 0, 64, 200, 39, 255, 1, 0, 0, 123 |
| `human_variable_pacing` | 6 | 113086, 49707, 6, 51, 10, 84, 1, 0, 0, 610 |
| | 16 | 293219, 275857, 16, 34, 6, 85, 1, 0, 0, 3645 |
| `tls_client_hello` | 6 | 3563, 3500, 6, 86, 33, 24, 1, 3, 2, 29 |

**`agent_constant_pacing`** has a gap of 2000 µs and alternates 400 B up / 1200 B down.

At 6 packets: `bytes_up = 1200` and `bytes_down = 3600`. Mean = `1200 >> floor_log2(6) = 1200 >> 2
= 300`. Ratio = `120000 >> floor_log2(3600) = 120000 >> 11 = 58`. Bucket = `0b11111100 = 252`.
Age = `10000 >> 10 = 9`.

**`human_variable_pacing`**, packets 2–6, gaps 100000, 20000, 350000, 5000, 150000 µs:

| Pkt | Δ | `iat_ewma` | `iat_var_ewma` |
|---|---|---|---|
| 2 | 100000 | 100000 (seed) | 0 |
| 3 | 20000 | 100000 − (80000>>3) = 90000 | 0 + (80000>>3) = 10000 |
| 4 | 350000 | 90000 + (260000>>3) = 122500 | 10000 + (250000>>3) = 41250 |
| 5 | 5000 | 122500 − (117500>>3) = 107813 | 41250 + (76250>>3) = 50781 |
| 6 | 150000 | 107813 + (42187>>3) = 113086 | 50781 − (8594>>3) = 49707 |

At 6 packets: `bytes_up = 206` and `bytes_down = 3900`, so mean = `206 >> 2 = 51` and ratio =
`20600 >> 11 = 10`. Bucket = bits 6, 4, 2 = 84. Age = `625000 >> 10 = 610`.

**`tls_client_hello`** has a ClientHello with SNI, ALPN `h2,…` and supported_versions in packet
4, so `reg_proto_meta = 0x0302`.

## 6. Verification responsibilities

- Python: `tests/test_feature_math.py`, `tests/test_extract_features.py` (hand cases), and
  `tests/test_feature_worked_examples.py` (fixture lock).
- P4: Dev A's PTF test sends each scenario's packets with the listed timestamps (or records the
  switch timestamps and replays those offline, see plan gap G14). It then compares
  `reg_flow_key[slot]`, all per-flow registers and the classifier-visible feature vector at each
  checkpoint.

## 7. Known approximations (state these in the paper)

- `mean_pkt_size_up` divides by a power of two that is ≤ `pkt_count` and counts both directions,
  so it over-reads the true upstream mean by less than 2×.
- `updown_ratio_x100` has power-of-two denominator steps.
- `flow_age_ms` is in 1.024 ms units.
- A ClientHello that spans more than one segment (common with post-quantum key shares) yields only
  the extensions inside the first segment.
- Offline sketch epochs start at the capture's first packet, while live epochs are controller-timed.
  `fanout_new_flows` is therefore bit-exact only within a single epoch.
