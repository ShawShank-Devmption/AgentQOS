# Feature Arithmetic Design Gaps

Task P2.4 requires Python preprocessing to reproduce P4 fixed-point behavior bit for bit before
P3.3 can be considered complete. Design section 4.3 freezes feature names, order, widths, and units,
while sections 5.2 and 7.3 define saturation and the IAT EWMA update. The following operations still
lack enough detail for two independent developers to produce identical values.

## DESIGN GAP: shift division

`mean_pkt_size_up` is described as `bytes_up / pkt_count via shift approx`, but the divisor selection
is unspecified for non-powers of two, including the required six-packet checkpoint. The design must
choose floor, ceiling, or nearest power of two and state whether `pkt_count` includes both directions.

`updown_ratio_x100` has a non-power-of-two numerator scale and a variable denominator. The design
must state the shift/add expansion for multiplication by 100, the denominator approximation, the
zero-downstream result, and the saturation point.

## DESIGN GAP: first-eight size signature

`first8_size_bucket` is an eight-bit coarse signature, but the encoding is unspecified. The design
must define packet-size bucket boundaries and how eight ordered observations collapse into eight
bits. This choice can materially change the trained tree and therefore belongs in P2.4 rather than an
implementation-local helper.

## DESIGN GAP: count-min sketch identity

The sketch dimensions and two epochs are fixed, but the four hash algorithms or seeds, counter
width, saturation value, and epoch boundary origin are not. Without these values, offline extraction
cannot reproduce P4 collision behavior for `fanout_new_flows`.

## DESIGN GAP: protocol metadata encoding

The ALPN classes are named `none`, `h1`, `h2`, and `other`, but their numeric encodings are not
assigned. The bounded TLS parser also needs the exact behavior for malformed/truncated extension
lengths before `tls_ext_count` and `tls_alpn_class` can be mirrored safely.

## Proposed decision process

Dev A and Dev B should select the smallest P4-feasible definitions, add worked examples at packet
counts 6, 16, and 64, and update design section 4 plus `common/contracts.py` through the documented
two-approval contract process. Only then should `ml/extract_features.py` and the corresponding P4
feature arithmetic be implemented. Until that decision, Review 1 uses the orchestration-to-label
pipeline as its initial preprocessing evidence and makes no feature-value or accuracy claim.
