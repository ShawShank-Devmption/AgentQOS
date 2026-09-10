"""Tests pinning the frozen design.md section 4 contracts."""

from pathlib import Path

from common.contracts import (
    BURST_INTENSITIES,
    CLASS_ACTION_TABLE,
    CLASS_TREATMENTS,
    CM_SKETCH_COLUMNS,
    CM_SKETCH_ROWS,
    COMPILED_TREE_ENTRY_FIELDS,
    COMPILED_TREE_FIELDS,
    CPU_HEADER_FIELDS,
    EXPERIMENT_CONFIG_FIELDS,
    EXPERIMENT_SYSTEMS,
    EXPERIMENT_TOPOLOGY,
    FEATURE_BIT_WIDTHS,
    FEATURE_CHECKPOINTS,
    FEATURE_ORDER,
    FLOW_OVERRIDE_TABLE,
    FLOW_SLOTS,
    LABEL_FIELDS,
    PUNT_FILTER_TABLE,
    REGISTER_NAMES,
    TABLE_NAMES,
    TRAINING_LABELS,
    TREE_TABLE_NAMES,
    TrafficClass,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_class_labels_and_treatments_match_design() -> None:
    assert {traffic_class.value for traffic_class in TrafficClass} == {0, 1, 2, 3}
    assert CLASS_TREATMENTS[TrafficClass.UNKNOWN].dscp == 0
    assert CLASS_TREATMENTS[TrafficClass.UNKNOWN].queue_priority == 1
    assert CLASS_TREATMENTS[TrafficClass.HUMAN_INTERACTIVE].dscp == 34
    assert CLASS_TREATMENTS[TrafficClass.HUMAN_INTERACTIVE].queue_priority == 2
    assert CLASS_TREATMENTS[TrafficClass.AGENT_INTERACTIVE].dscp == 18
    assert CLASS_TREATMENTS[TrafficClass.AGENT_INTERACTIVE].queue_priority == 1
    assert CLASS_TREATMENTS[TrafficClass.AGENT_BULK].dscp == 8
    assert CLASS_TREATMENTS[TrafficClass.AGENT_BULK].queue_priority == 0


def test_p4_controller_names_match_design() -> None:
    assert tuple(f"tbl_tree_l{level}" for level in range(8)) == TREE_TABLE_NAMES
    assert FLOW_OVERRIDE_TABLE == "tbl_flow_override"
    assert CLASS_ACTION_TABLE == "tbl_class_action"
    assert PUNT_FILTER_TABLE == "tbl_punt_filter"
    assert len(TABLE_NAMES) == 11
    assert "reg_flow_key" in REGISTER_NAMES
    assert "reg_proto_meta" in REGISTER_NAMES
    assert "policy_version" in REGISTER_NAMES
    assert "reg_collision_ctr" in REGISTER_NAMES


def test_state_and_feature_contracts_match_design() -> None:
    assert FLOW_SLOTS == 65_536
    assert (CM_SKETCH_ROWS, CM_SKETCH_COLUMNS) == (4, 4_096)
    assert FEATURE_CHECKPOINTS == (6, 16, 64)
    assert FEATURE_ORDER == (
        "iat_ewma_us",
        "iat_var_ewma",
        "pkt_count",
        "mean_pkt_size_up",
        "updown_ratio_x100",
        "first8_size_bucket",
        "fanout_new_flows",
        "tls_ext_count",
        "tls_alpn_class",
        "flow_age_ms",
    )
    assert FEATURE_BIT_WIDTHS == (32, 32, 32, 16, 16, 8, 16, 8, 8, 32)


def test_serialization_schemas_match_design() -> None:
    assert LABEL_FIELDS == (
        "flow_id",
        "src_ip",
        "dst_ip",
        "proto",
        "src_port",
        "dst_port",
        "label",
        "source_framework",
        "pcap_file",
    )
    assert tuple(label.value for label in TRAINING_LABELS) == (1, 2, 3)
    assert COMPILED_TREE_FIELDS == ("model_hash", "feature_order", "entries")
    assert COMPILED_TREE_ENTRY_FIELDS == (
        "table",
        "match_ranges",
        "action",
        "action_params",
        "priority",
    )
    assert EXPERIMENT_CONFIG_FIELDS == (
        "name",
        "system",
        "topology",
        "link_mbps",
        "agent_share_pct",
        "burst_intensity",
        "duration_s",
        "seeds",
        "outputs",
    )
    assert EXPERIMENT_SYSTEMS == ("ours", "fifo", "diffserv", "fairq", "app_limiter")
    assert EXPERIMENT_TOPOLOGY == "choke_v1"
    assert BURST_INTENSITIES == ("low", "med", "high")


def test_cpu_header_layout_matches_design() -> None:
    assert CPU_HEADER_FIELDS == (
        ("flow_hash", 16),
        ("class", 8),
        ("reason", 8),
        ("ingress_port", 16),
        ("pad", 16),
    )
    assert sum(width for _, width in CPU_HEADER_FIELDS) == 64


def test_agent_instruction_files_remain_identical() -> None:
    assert (PROJECT_ROOT / "AGENTS.md").read_bytes() == (PROJECT_ROOT / "CLAUDE.md").read_bytes()


def test_frozen_repository_layout_exists() -> None:
    expected_directories = (
        "p4src",
        "controller",
        "ml",
        "harness",
        "eval",
        "dashboard",
        "common",
        "tests",
        "docs",
    )
    assert all((PROJECT_ROOT / directory).is_dir() for directory in expected_directories)
