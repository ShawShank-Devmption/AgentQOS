# P2.1 parser status and integration contract

`p4src/headers.p4` and `p4src/parser.p4` now compile with the pinned p4c
image. The parser extracts Ethernet, fixed IPv4, TCP, UDP, and bounded TCP
options. It enters the TLS path when the payload starts with a TLS
handshake-record marker. It then checks record and
handshake lengths, parses the fixed ClientHello fields, and bounds the
session ID (32 bytes), cipher suite list (512 bytes), compression methods
(32 bytes), and extension traversal (16 extensions, 512 bytes per extension).

The parser leaves any unconsumed payload in the packet buffer. The deparser
emits every extracted header in wire order, including variable-length TLS
fields. The PTF suite sends valid, malformed, and over-bound packets through
BMv2 and compares forwarded bytes exactly. A test-only register probe also
asserts the parser metadata; it is absent from the production P4 build. The
suite covers a zero- and a
nonzero-length session ID, extension vectors of 1, 16, and 17 entries,
declared-length mismatches, TCP options, malformed ALPN/SNI vectors, and a
513-byte extension.

`meta.feature_valid` defaults to zero. The ordinary IPv4/TCP/UDP path sets it
only after fixed-header length checks and a comparison of the declared IPv4
length against `standard_metadata.packet_length` (the supported v1model
length source). A malformed TLS length clears it;
ingress also clears it on any parser error (§7.15). A 17th extension or an
oversized individual extension sets `meta.tls_truncated`, stops deeper
inspection, and leaves the packet forwarding. Neither case drops traffic.

For a complete bounded ClientHello, the parser produces `tls_cipher_count`,
`tls_ext_count`, `tls_truncated`, ALPN presence and one-hot h1/h2/other flags,
and the first SNI hostname length. The ALPN flags describe the first
advertised protocol. The parser-local SNI bucket is 0 for absent, 1 for
1–32 bytes, 2 for 33–128 bytes, and 3 for longer names. These parser-local
values are not yet a frozen `reg_proto_meta` layout or an approved numeric
`tls_alpn_class` encoding.

## Handoff to the feature stage

| Parser output | Meaning for ingress |
|---|---|
| `feature_valid` | Zero means UNKNOWN/fail-open and no feature-state write (§7.15). |
| `tcp_payload_present` | Candidate for the first-observed-payload decision; the parser cannot determine flow ownership. |
| `tls_record_candidate` | Complete bounded ClientHello prefix was parsed, or extension traversal stopped at its bound. |
| `tls_cipher_count`, `tls_ext_count`, `tls_truncated` | Provisional counts and bounded-traversal status; use only when `feature_valid` and `tls_record_candidate` are set. |
| ALPN one-hot flags, SNI length/bucket | Provisional semantic values for `reg_proto_meta`; do not serialize them until the frozen layout and numeric ALPN mapping are approved. |

The P3.1 ingress stage should claim the flow slot first, then use
`tcp_payload_present` to determine the first eligible payload packet. A SYN
does not consume this opportunity. The precise `reg_proto_meta` seen-bit
layout and malformed-first-payload rule remain part of the contract review;
P2.1 does not write registers.

## Downstream integration gates

- Approve the numeric `tls_alpn_class` encoding and packed `reg_proto_meta`
  layout before ingress stores or the controller reads these provisional
  parser fields. The parser-local SNI bucket boundaries also need review.
- Resolve which later packet may commit TLS metadata when the first observed
  packet is a SYN or an incomplete payload. Parser-only state cannot answer
  that question; ingress must coordinate with slot ownership.
- Add an integrated PTF check of `reg_proto_meta` after P3.1 implements slot
  ownership and the approved first-payload commit rule. The current P2.1
  tests assert parser metadata through a test-only probe.

**DESIGN GAP:** §5.1 asks for the first observed TLS payload, while the
parser runs before flow ownership is known. The proposed parser/ingress split
is in `docs/feature_stage_register_map.md`; Person 1 and Person 2 must approve
its commit rule before `reg_proto_meta` is implemented.
