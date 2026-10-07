# Lab Notebook

## 2026-09-09 - Review 1 baseline and first implementation slice

Read `AGENTS.md`, `requirements.md`, `design.md`, `tasks.md`, the project-details PDF/HTML, and all
11 pages of the supplied Course Project Review 1 template before planning or editing source code.
The review template requires the title, objectives, gap, proposed system, architecture, tools,
modules, module input/process/output, algorithm, and visible evidence of roughly 25% progress.

The selected Review 1 slice is the shared contract foundation plus a narrow vertical prototype:

- P0.2/P0.3/P0.5/P0.6: repository scaffold, frozen Python contracts, instruction-file equality,
  lint, and tests;
- P1.5: minimal L2 P4 source, deterministic `choke_v1` topology, and a BMv2 policy-version read;
- P2.6: strict validation of the frozen experiment YAML shape and a 30-second smoke config; and
- initial P3.4/P3.5 data work: orchestration windows joined to pcap 5-tuples to produce the frozen
  `labels.csv` schema without feature-derived labels (design section 7.20).

Host verification after these changes: Ruff and 34 pytest tests pass. The experiment smoke config
validates.
The macOS host lacks p4c, BMv2, Mininet, tshark, tcpreplay, and iperf3. Docker CLI is installed, but
the `p4lang/p4c:latest` image download did not complete on the available connection, so the P4
compile and live Mininet/iperf M1 check remain Linux/runtime verification items and must not be
presented as completed evidence.

DESIGN GAP: P2.4 cannot yet define bit-exact feature extraction because the shift-division rules,
first-eight size encoding, count-min sketch hashes/seeds, and ALPN numeric encoding are absent from
the frozen design. Details and the required contract process are recorded in
`docs/feature_arithmetic_design_gaps.md`.

## 2026-10-07 - P2.4 feature arithmetic proposed (Dev B)

`docs/feature_arithmetic.md` now gives exact fixed-point definitions for all ten features. It
covers the shift division, the ×100 expansion, the first-8 bitmask (MSB = first packet, ≥128 B),
crc32+salt hashing, the ALPN enum, ClientHello bounds, and IAT seeding. The Python reference is
`common/feature_math.py` plus `ml/extract_features.py`. Worked examples at 6/16/64 packets are in
`tests/fixtures/feature_worked_examples.json`. Contract additions are in `common/contracts.py`,
including the new `reg_first_ts` register, which is a frozen-contract change awaiting 2 approvals.

**Pending:** Dev A agreement and a PTF replay of the fixture through `features.p4`. P2.4 is not
done until that replay matches exactly.
