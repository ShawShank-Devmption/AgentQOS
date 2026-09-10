# Review 1 Implementation Status

**Project:** Agent-Aware Networking  
**Review date:** 9 September 2026  
**Milestone:** First 25% foundation and prototype slice

## Verified evidence

Run the complete host evidence gate from the repository root:

```bash
make review-1-host
```

Current result:

- Ruff lint and formatting checks pass.
- 34 pytest tests pass.
- `eval/configs/review_1_smoke.yaml` validates as a seeded 30-second `choke_v1` run.
- `AGENTS.md` and `CLAUDE.md` are byte-identical.

The implemented artifacts are:

- an implementation of the frozen shared contracts for class treatments, P4/controller identifiers,
  feature order, CPU header, labels, compiled-tree output, and experiment configuration, awaiting
  the task P0.3 team sign-off;
- a minimal v1model L2 forwarding program containing the M1 forwarding table and readable policy
  version register;
- deterministic `choke_v1` host, port, and bottleneck metadata;
- a controller-only BMv2 CLI boundary with validated register reads and explicit error reporting;
- orchestration-derived pcap flow labeling in the frozen `labels.csv` schema; and
- strict experiment YAML loading plus a Review 1 smoke configuration.

## Evidence that still needs the Linux runtime

The current macOS host does not contain p4c, BMv2, Mininet, tshark, tcpreplay, or iperf3. Docker was
started, but the large `p4lang/p4c:latest` download did not finish on the available connection.
Therefore the following commands remain external verification and must not be shown as passed:

```bash
make build
make dev-env
python -m controller.app --thrift-port 9090
```

The final M1 evidence is one iperf3 flow across the BMv2 switch followed by a successful live read of
`policy_version[0]`.

## Slide 11 wording

“The first implementation slice now has a frozen and tested interface layer, a minimal P4 forwarding
program, the `choke_v1` topology definition, controller register-read handling, orchestration-based
pcap labeling, and seeded experiment configuration. All 34 host-side tests and style checks pass.
P4 compilation and live Mininet/iperf execution are awaiting the documented Linux toolchain; no
classification or performance result is claimed yet.”

## Design decision needed next

Bit-exact feature preprocessing cannot proceed safely until the team freezes the shift-division
rules, first-eight packet-size encoding, count-min sketch hashes/seeds, and ALPN numeric encoding.
These are listed in `docs/feature_arithmetic_design_gaps.md` for the next team sync and contract PR.
