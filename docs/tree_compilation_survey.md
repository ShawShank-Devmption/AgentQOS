# P1.1 — Stream B tree-compilation survey

**Owner:** Dev A (DATAPLANE)  
**Task:** P1.1 in `tasks.md`  
**Design contracts consumed:** `design.md` §§4.2–4.5 and §5.3; `common/contracts.py`
(`TREE_TABLE_NAMES`, `FEATURE_ORDER`, `WARMUP_PACKETS`, and compiled-tree fields).  
**Decision produced:** the constraints for Dev B's P3.6 tree compiler and Dev A's P3.10
classifier wiring. This note does not change a frozen contract.

## Decision

Use a single depth-maximum-eight decision tree. Compile exactly one logical tree level into each
of the predeclared `tbl_tree_l0` through `tbl_tree_l7` tables. A table entry range-matches the
frozen, integerized feature selected by the node and advances the in-packet traversal state to
the child or writes a class at a leaf. The generated section emits both the fixed table
declarations and entries, while the controller loads entries in the frozen compiled-tree JSON
schema. `tbl_flow_override` remains ahead of this pipeline and wins over it.

This is the IIsy/Mousika-style match-action mapping required by design §5.3. It preserves the
design's depth cap and table names, keeps inference in the data plane, and lets a policy update
replace entries without changing P4 source. It deliberately does not adopt random forests,
teacher/student distillation, or a general ML mapping framework: those add state, tables, and
verification scope outside the task's fixed classifier contract.

### Required compiler safeguards

1. Train and export only an axis-aligned sklearn tree of maximum depth eight using
   `FEATURE_ORDER` unchanged. Thresholds must be converted to the exact fixed-point integer
   domain before entries are emitted.
2. Emit no entry whose table is outside `TREE_TABLE_NAMES`; every entry must have the frozen
   `model_hash`, `feature_order`, and entry fields.
3. Represent each branch as disjoint inclusive ranges covering its valid feature domain. A leaf
   writes one of the existing two-bit `TrafficClass` values. Do not assign UNKNOWN to training
   leaves.
4. Preserve design §5.3: before `WARMUP_PACKETS`, use UNKNOWN unless an exact-flow override
   matches. The compiler does not encode a competing warm-up rule.
5. Make `ml/verify_equivalence.py` simulate the emitted ranges and require 100% agreement with
   sklearn on held-out integerized rows (design §§7.9 and 8.3). It must additionally detect
   overlapping ranges, unreachable leaves, and missing path coverage (design §7.14).

## Notes by paper/system

### pForest — In-Network Inference with Random Forests (2019)

pForest trains phase-specific random forests and switches among them as a flow accumulates
packets, classifying “as soon as possible.” Its important contribution here is the recognition
that early-flow confidence and feature availability vary over time. Its random-forest ensemble
and dynamic model switching are inappropriate for the fixed eight-table contract: they multiply
table/state cost. We retain the compatible operational idea as the existing six-packet warm-up
and later controller reclassification, not as pForest-style per-phase forests.

Primary source: [arXiv:1909.05680](https://arxiv.org/abs/1909.05680).

### Planter — Automating In-Network Machine Learning (2022)

Planter is a general model-to-data-plane generation framework with multiple mappings and target
back ends. It demonstrates the value of generating both P4 artifacts and table entries from a
single model representation. We apply only that artifact-consistency lesson: P3.6 must generate
the classifier declaration section and the table-entry JSON together. We do not import Planter
or its configuration framework, since the repository has a specified sklearn-to-BMv2 path and
does not permit new dependencies without agreement.

Primary sources: [paper](https://arxiv.org/abs/2205.08824) and
[artifact](https://github.com/In-Network-Machine-Learning/Planter).

### Mousika — Knowledge-Distilled In-Network Intelligence (INFOCOM 2022)

Mousika transforms decision trees into binary decision trees to make them more suitable for
match-action pipelines, and can distil a larger teacher model into that deployable student. It
is the closest motivation for retaining a shallow tree and mapping its branch decisions to
tables. Distillation and an alternative BDT representation are explicitly out of scope: the
frozen design fixes sklearn depth at eight and names level tables already. If Phase 4 accuracy
is insufficient, changing to a distilled model is a design change requiring team approval, not
an implementation shortcut.

Primary source: [IEEE INFOCOM record, DOI 10.1109/INFOCOM48880.2022.9796936](https://doi.org/10.1109/infocom48880.2022.9796936).

### IIsy — Practical In-Network Classification (2022/2024)

IIsy maps trained classifiers to match-action pipelines and uses a control plane to load the
generated mapping. Its decision-tree approach motivates a fixed pipeline whose entries, rather
than P4 control flow, express the trained model. This is the direct structural precedent for
the chosen one-level-per-table recipe. IIsy's reported resource pressure is a warning: a
compiler must reject a model that exceeds the eight fixed levels rather than silently emitting
an incomplete classifier.

Primary sources: [HotNets artifact](https://github.com/cucl-srg/IIsy) and
[journal version](https://doi.org/10.1109/TNET.2024.3364757).

### NetBeacon — Efficient Intelligent Network Data Plane (USENIX Security 2023)

NetBeacon combines flow-level and packet-level features through sequential models and proposes
an efficient representation to avoid tree-table entry explosion. This validates the project’s
choice to retain flow-state features such as inter-arrival statistics rather than relying only
on packet headers. Its warning is directly actionable: P3.6 must report entry count and
P4.3 must report classifier table footprint. NetBeacon's alternate representation is not
adopted because it would violate the frozen eight-level-table interface.

Primary source: [USENIX Security paper](https://www.usenix.org/system/files/sec23summer_422-zhuo_guangmeng-prepub.pdf).

### ACC-Turbo — Aggregate-Based Congestion Control (SIGCOMM 2022)

ACC-Turbo performs in-switch online clustering of high-bandwidth traffic aggregates and uses
programmable scheduling to mitigate pulse-wave DDoS. It is not a supervised decision-tree
compiler, but it is the closest action precedent: a line-rate classification signal drives
queueing/rate treatment in the same switch. Our use is narrower and fail-open: uncertain traffic
is best effort, and only AGENT_BULK is eligible for a meter-red drop. No ACC-Turbo clustering or
attack-specific mitigation logic belongs in the classifier.

Primary sources: [SIGCOMM record](https://doi.org/10.1145/3544216.3544263) and
[reproducibility artifact](https://github.com/nsg-ethz/ACC-Turbo).

## Integration handoff

P3.6 (Dev B) should implement the compiler safeguards above and use this decision as the
generation contract. P3.10 (Dev A) should wire the generated levels only after the P4 target is
available and compiler errors can be supplied, per the project P4 workflow. P4.3 should include
the generated entry count and table/register footprint in its resource report.
