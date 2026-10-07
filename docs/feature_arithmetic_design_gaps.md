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
