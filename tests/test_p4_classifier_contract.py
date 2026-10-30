"""Guard the generated classifier table keys and frozen override action."""

from pathlib import Path

from common.compiled_tree import classify, load_compiled_tree
from common.contracts import FEATURE_ORDER, TrafficClass
from ml.compile_tree import GENERATED_BEGIN, GENERATED_END, emit_p4_declarations


def test_classifier_tables_are_generated_from_feature_contract() -> None:
    source = Path("p4src/classifier.p4").read_text()
    generated = source.split(GENERATED_BEGIN, 1)[1].split(GENERATED_END, 1)[0].strip()
    assert generated == emit_p4_declarations().strip()
    assert "action set_flow_class(bit<2> class)" in source
    assert "tbl_flow_override" in source


def test_feature_stage_exposes_all_frozen_tree_inputs() -> None:
    source = Path("p4src/features.p4").read_text()
    for name in (
        "mean_pkt_size_up",
        "updown_ratio_x100",
        "first8_size_bucket",
        "fanout_new_flows",
    ):
        assert f"meta.features.{name} =" in source


def test_qos_has_egress_hysteresis_and_interactive_fail_open() -> None:
    egress = Path("p4src/qos_egress.p4").read_text()
    ingress = Path("p4src/agent_aware.p4").read_text()
    assert "CONGESTION_HIGH_PACKETS" in egress
    assert "CONGESTION_LOW_PACKETS" in egress
    assert "meta.traffic_class == CLASS_AGENT_BULK" in ingress
    assert "mark_to_drop(standard_metadata)" in ingress


def test_smoke_tree_is_explicitly_untrained_and_fail_open() -> None:
    tree = load_compiled_tree(Path("tests/fixtures/smoke_tree.json"))
    assert tree.model_hash == "smoke_only_untrained"
    assert classify(tree, [0] * len(FEATURE_ORDER)).traffic_class == TrafficClass.UNKNOWN


def test_bulk_meter_drop_cannot_be_undone_by_l2_forwarding() -> None:
    for program in ("agent_aware", "diffserv"):
        source = Path(f"p4src/{program}.p4").read_text()
        assert source.index("tbl_l2_forward.apply()") < source.index(
            "mark_to_drop(standard_metadata)"
        )
