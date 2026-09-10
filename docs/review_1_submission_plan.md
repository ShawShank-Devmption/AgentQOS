# Review 1 Submission Plan - Agent-Aware Networking

**Review date:** 9 September 2026  
**Milestone:** First 25% - foundation, initial preprocessing, and M1 forwarding prototype  
**Source of review structure:** `Beamer_Presentation__3_.pdf`  
**Authoritative project sources:** `requirements.md`, `design.md`, and `tasks.md`

## 1. Scope of the 25% milestone

The Review 1 milestone will demonstrate a coherent vertical foundation rather than claim partial
completion of every final subsystem. It covers the shared contracts, reproducible repository
scaffold, a minimal P4/BMv2 forwarding path, the Mininet topology definition, a controller-to-switch
read path, and deterministic initial traffic-corpus labeling.

The milestone consumes the frozen contracts in design section 4 and does not change them:

- class labels and class treatments from section 4.1;
- P4 table names, register names, slot count, and CPU punt header from section 4.2;
- the ten-feature order and integer units from section 4.3;
- the corpus label and compiled-tree schemas from sections 4.4 and 4.5; and
- the experiment configuration shape from section 4.6.

### Included task IDs

| Task | Review 1 deliverable | Evidence |
|---|---|---|
| P0.2 | Complete the frozen repository layout and useful Make targets | Directory tree and CI checks |
| P0.3 | Implement `common/contracts.py` as the shared source of truth | Contract unit tests |
| P0.5 | Preserve byte-identical `AGENTS.md` and `CLAUDE.md` | Automated equality check |
| P0.6 | Keep Ruff lint and formatting checks green | `make lint` output |
| P1.5 | Build M1: minimal L2 forwarding, `choke_v1` topology, switch register read | P4 compile, Python tests, and smoke instructions |
| P2.4 / P3.3 | Record the contract-adjacent arithmetic gaps that prevent safe bit-exact implementation | Gap note routed through the frozen-contract process |
| P2.6 (initial slice) | Validate the frozen experiment YAML schema and add a Review 1 smoke config | Config tests and validation command |
| P3.4 / P3.5 (initial slice) | Join orchestration windows to captured 5-tuples using the frozen label schema | Deterministic pcap fixture and CSV tests |

The full TLS ClientHello parser, production feature registers, real agent-framework corpus, trained
tree, QoS actions, and evaluation grid remain in their scheduled later milestones. Review 1 will not
present synthetic or fixture traffic as the final research corpus.

## 2. Behavior and invariant trace before implementation

1. Packets entering the M1 switch are forwarded by an explicit destination-MAC table. Table miss
   behavior is best-effort flooding or forwarding defined by the topology, never a classification
   drop.
2. The controller reads switch state only through `controller/switch_api.py`, preserving the single
   switch-API ownership rule from design section 4.2.
3. Unknown or incomplete traffic remains class `UNKNOWN`, DSCP 0, queue 1. This preserves the
   fail-open requirement in requirements NFR-6.
4. When feature extraction is implemented, it must follow the frozen feature order. Integer
   arithmetic saturates instead of wrapping, first-packet and zero-delta IAT samples are skipped
   (design sections 7.3 and 7.12), and checkpoints occur at exactly 6, 16, and 64 packets.
5. Labels originate from orchestration metadata. Fixture labels exist only to test the pipeline and
   are clearly marked as fixtures, preserving design section 7.20.
6. No final paper number or performance claim will be inferred from a fixture. Experimental numbers
   must later trace to an experiment config and seed.

## 3. Implementation sequence and gates

### Gate A - Shared foundation

- Create only the top-level directories frozen in design section 3.
- Implement the constants and schema validators in `common/contracts.py`.
- Add tests for labels, mappings, schemas, and instruction-file equality.
- Run formatting, lint, and pytest before depending on the contracts.

### Gate B - M1 forwarding prototype

- Add a small v1model L2 forwarding program in `p4src/l2fwd.p4`.
- Add the `choke_v1` host/switch/link specification in `harness/topology.py`.
- Add the minimal switch API read path and controller entrypoint needed to read the policy version.
- Add focused Python tests for topology and CLI-output parsing.
- Compile the P4 program in the supported Linux toolchain or the project P4 container. Record any
  environment limitation instead of reporting an unverified compile.

### Gate C - Initial preprocessing

- Trace and record any missing arithmetic definition that blocks bit-exact P4/Python agreement.
- Implement orchestration-window-to-pcap flow joining using the frozen label schema.
- Add tests for TCP/UDP extraction, deterministic flow ordering, ambiguous windows, invalid labels,
  and exact CSV column order.
- Use a small generated pcap fixture only as test evidence, never as the final research corpus.

### Gate D - Review evidence

- Run `make lint`, `make test`, and the smallest available P4 build/smoke check.
- Record commands, outputs, limitations, and the current task status in `docs/notebook.md`.
- Prepare a concise Review 1 status table distinguishing implemented, host-verified,
  Linux/BMv2-verified, and planned items.

## 4. Review presentation outline

The presentation should follow the supplied 11-page Beamer template while replacing its teaching
examples with project-specific content.

| Slide | Heading | Project content and evidence |
|---|---|---|
| 1 | Course Project Review 1 | Project title, team members, guide, course, and review date |
| 2 | Project Title and Scope | “Agent-Aware Networking: In-Network Classification and Differentiated Transport QoS for AI-Agent Traffic” plus the one-line scope |
| 3 | Objectives | Line-rate classification, three-class QoS, controller refinement, and measurable p99/overhead targets |
| 4 | Gap Analysis | Application-edge agent detection versus the desired forwarding-plane classification-to-QoS coupling |
| 5 | Proposed System | P4 switch classifies each packet and immediately selects DSCP, queue, ECN profile, and meter; Ryu refines decisions |
| 6 | Architecture | Traffic sources -> Mininet/BMv2 pipeline -> target, with controller and offline ML paths |
| 7 | Tools | P4_16, BMv2, Mininet, Python 3.11, Ryu/Python 3.9, scikit-learn, Scapy, pytest, tshark, pandas, and matplotlib |
| 8 | Modules | Data plane, controller, ML, traffic harness, evaluation, and dashboard |
| 9 | Module Input/Process/Output | Compact table for each module, emphasizing the implemented Review 1 subset |
| 10 | Algorithm | Decision-tree inference as range-match tables; current orchestration-to-flow label join; fixed-point choices pending team freeze |
| 11 | First 25% Evidence | Contracts/tests, P4 M1 source and compile status, topology, controller parser, label fixture, test summary, and next milestone |

## 5. Review-ready acceptance checklist

- [ ] Frozen contract tests pass.
- [ ] `AGENTS.md` and `CLAUDE.md` remain byte-identical.
- [ ] Ruff check and format check pass.
- [ ] Python unit tests pass on the host.
- [ ] `l2fwd.p4` compiles with `p4c-bm2-ss` in Linux/container tooling.
- [ ] `choke_v1` can be launched in the documented Linux VM, or the missing external environment
      check is explicitly recorded.
- [ ] Controller parsing demonstrates a policy-version register read.
- [ ] Fixture evidence is clearly separated from the future real agent/human corpus.
- [ ] The Review 1 status slide reports only verified results.

## 6. Implementation status - 9 September 2026

- [x] Frozen contract tests pass.
- [x] `AGENTS.md` and `CLAUDE.md` are byte-identical.
- [x] Ruff check and format check pass.
- [x] Python unit tests pass on the host (34 tests).
- [ ] `l2fwd.p4` compile: source is implemented; the CI image download did not complete on the
      available connection.
- [ ] Live `choke_v1` launch: requires the Linux VM with Mininet, BMv2, and iperf3.
- [x] Controller register-response parsing is tested; a live switch read remains part of the Linux
      check above.
- [x] The orchestration-to-`labels.csv` preprocessing fixture passes without feature-derived labels.
- [x] The Review 1 smoke experiment configuration validates.
- [x] Bit-exact feature arithmetic remains explicitly blocked by the P2.4 design gaps recorded in
      `docs/feature_arithmetic_design_gaps.md`.

## 7. Risks and review wording

- Mininet and BMv2 do not run natively on the current macOS host. Linux VM or container verification
  is required before claiming the live M1 demo.
- The BMv2 and p4c commit hashes are still awaiting the first team Linux installation; the review
  should describe `docs/ENVIRONMENT.md` as the environment procedure, not a completed three-person
  setup.
- Real Browser Use, Playwright-agent, AutoGen, Claude+MCP, MAWI, and CAIDA captures belong to P3.4,
  P3.5, and P3.8. Review 1 may show the ingestion contract and fixture, but must not claim the final
  corpus has been collected.
- The milestone demonstrates engineering progress. It does not yet support classification accuracy,
  p99-reduction, or per-packet-overhead claims.
