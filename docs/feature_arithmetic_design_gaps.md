# Feature Arithmetic Design Gaps

**Resolved by P2.4 (proposal):** see `docs/feature_arithmetic.md`. That document now defines the
shift division, first-eight size encoding, count-min sketch hashing, and ALPN encoding raised
here. They mirror `common/feature_math.py` and carry worked examples at 6, 16, and 64 packets.

Still open until approved:

- G1 — `reg_first_ts` is a new frozen register. This is a contract change and needs 2 approvals.
- G3 — the canonical bidirectional flow key is chosen by ingress port. Dev A must confirm.
- G4 — BMv2 `crc32` must equal `zlib.crc32`. The first PTF run of the worked-example fixture
  verifies this.
- G17 — sketch epochs are controller-timed, so `fanout_new_flows` is bit-exact offline only
  within a single epoch.
- G18 — the rule "parse only the flow's first payload packet" (§1 step 7 of
  `docs/feature_arithmetic.md`) needs a per-slot "first payload seen" bit. `reg_proto_meta == 0`
  cannot tell "no payload yet" apart from "first payload was not TLS", and the v1model parser
  cannot read registers, so ingress must gate the write on that bit. The Python mirror models it
  as `FlowState.payload_seen`. Proposal: bit 15 of `reg_proto_meta` (`ext_count` ≤ 16 needs only
  bits 8–12). The fixture's packed `reg_proto_meta` would then carry `0x8000` once any payload
  is seen. This needs Dev A's agreement before the PTF replay.
