# P2.1 parser progress and verification

This increment implements design §5.1 Ethernet/IPv4/TCP/UDP parsing and the base-protocol
fail-open paths from §7.15. It consumes the generated `headers.p4` wire layouts and class
constants; it does not change frozen §4 contracts. **P2.1 remains incomplete:** bounded TLS
ClientHello parsing is still required.

## Implemented behavior

- Initialize all feature values, parser length temporaries and `tree_node` to zero;
  initialize `traffic_class` to generated `CLASS_UNKNOWN` and `feature_valid` to zero.
- Extract Ethernet only when all 14 bytes exist. Non-IPv4 accepts without features.
- Extract the fixed IPv4 header only when all 20 bytes exist. Require version 4, IHL ≥ 5,
  total length ≥ header length, and the entire declared IP datagram within the frame.
  Ethernet padding beyond the declared datagram is allowed and preserved.
- Skip features for fragmented packets, including a first fragment with MF set, and for
  unsupported transports such as ICMP. There are no state/register writes in this include.
- Preserve IPv4 and TCP options in existing variable-width headers, bounded to 40 bytes
  each. Never advance over bytes that the deparser cannot reproduce.
- Require the whole fixed TCP/UDP header inside the declared IP datagram. Validate TCP
  data offset against the transport length; validate UDP length ≥ 8 and ≤ transport length.
  Set `feature_valid=1` only after successful transport parsing, leaving class UNKNOWN for
  the later classifier. UDP bytes outside its declared length remain opaque and preserved.
- Every branch terminates at `accept`. Each extraction is preceded by length checks;
  malformed packets retain extracted complete headers plus their unconsumed payload.

The integrating deparser **must** emit Ethernet, IPv4, IPv4 options, TCP, TCP options and UDP
in that order. An invalid header emits nothing. The original remainder is appended by BMv2.
The integrating pipeline must gate feature-register access on `feature_valid`.

DESIGN GAP: §5.1 explicitly names fragment offsets greater than zero, while §7.15 names
fragments generally. This increment conservatively skips both first and later fragments:
their incomplete transport payloads cannot safely participate in TLS parsing. Forwarding is
unchanged. No contract change is required.

## Compiler evidence

The existing local `p4lang/p4c:latest` Docker image was used with `--pull=never` and
`--platform linux/amd64`; no remote compiler or image download was used. Compilation passed
first for the current generated headers, then after each parser increment (IPv4, TCP, UDP),
and finally for the instrumented PTF harness. The compiler reported only unused constants
in the isolated harness. This is compiler evidence, not BMv2 runtime evidence.

`make lint` passed after adding the parser tests. The targeted host command
`.venv/bin/pytest tests/test_generate_headers.py tests/ptf/test_parser.py` reports
**4 passed, 1 skipped**. PTF is unavailable (pytest exits 5 when this skipped module is the
only requested module). **No PTF forwarding test has been run or passed here.**

## Reproduce the isolated test harness

The harness lives in ignored `build/`; no production `agent_aware.p4` is introduced. These
commands adapt the existing L2 skeleton, keep its empty checksum/egress controls, replace
ingress with fixed forwarding to port 1, and expose two test-only observation registers:

```sh
mkdir -p build
python3 - <<'PY'
from pathlib import Path

source = Path("p4src/l2fwd.p4").read_text()
start = source.index("header ethernet_t")
end = source.index("control VerifyChecksumControl", start)
source = (source[:start] + '#include "../p4src/headers.p4"\n'
          '#include "../p4src/parser.p4"\n\n' + source[end:])
start = source.index("control IngressPipeline(")
end = source.index("control EgressPipeline(", start)
source = source[:start] + '''control IngressPipeline(
    inout headers_t hdr,
    inout metadata_t meta,
    inout standard_metadata_t standard_metadata)
{
    @name("parser_feature_valid") register<bit<1>>(1) parser_feature_valid;
    @name("parser_class") register<bit<2>>(1) parser_class;
    apply {
        parser_feature_valid.write(0, meta.feature_valid);
        parser_class.write(0, meta.traffic_class);
        standard_metadata.egress_spec = 1;
    }
}

''' + source[end:]
source = source.replace("packet.emit(hdr.ethernet);", "\n        ".join(
    f"packet.emit(hdr.{header});" for header in
    ("ethernet", "ipv4", "ipv4_options", "tcp", "tcp_options", "udp")))
Path("build/parser_check.p4").write_text(source)
PY
docker run --rm --pull=never --platform linux/amd64 \
  -v "$PWD:/work" -w /work p4lang/p4c:latest \
  p4c-bm2-ss --std p4-16 -I p4src -o build/parser_check.json build/parser_check.p4
```

On the Linux BMv2/PTF development machine, connect switch interfaces `s1-eth0` and
`s1-eth1` to PTF interfaces `ptf0` and `ptf1` using two veth pairs. Disable offloads and do
not add padding to test frames; the physical-truncation cases require the actual sent length.
Run the switch and PTF with root networking privileges:

```sh
simple_switch --thrift-port 9090 -i 0@s1-eth0 -i 1@s1-eth1 build/parser_check.json
# In another terminal from the repository root:
PYTHONPATH="$PWD" ptf --test-dir tests/ptf --interface 0@ptf0 --interface 1@ptf1 \
  --test-params='ingress_port=0;egress_port=1;thrift_port=9090'
```

`ParserForwardingTest` sends 24 concrete frames through BMv2, verifies every output byte,
and reads the two observation registers via `simple_switch_CLI` after each frame. Cases
cover valid TCP/UDP, Ethernet padding, maximal IP/TCP options, combined options, non-IPv4,
first/later fragments, ICMP, wrong IP version, short IHL, invalid total lengths, invalid TCP
offsets, missing/partial transports, invalid UDP lengths, and physically truncated Ethernet,
IPv4 and options. Register reads fail explicitly if the test harness is not loaded.

## Remaining P2.1 work

- TLS record and ClientHello shallow headers, declared-length bounds and a maximum of 16
  extension bodies; cipher count, extension count, ALPN class, SNI bucket and truncation.
- Malformed TLS → `feature_valid=0`, UNKNOWN, best-effort, no feature-state writes; TLS
  tests including fragmented ClientHello payload, invalid nested lengths and extension cap.
- Resolve the already documented first-payload-seen state gap G18 in
  `docs/feature_arithmetic_design_gaps.md`. A parser cannot consult ingress-owned flow-slot
  registers before ingress; selecting the first observed payload needs an agreed pipeline
  approach. This increment does not invent that contract or claim to enforce it.
- Run the PTF cases above and add TLS cases once the next small parser increment compiles.
- Integrate a complete top-level pipeline only after feature/classifier/QoS stages exist.

Until the TLS increment lands, TCP payloads remain opaque. In particular, malformed TLS is
not yet distinguished from ordinary valid TCP, so the full §7.15 TLS invariant is not met.
