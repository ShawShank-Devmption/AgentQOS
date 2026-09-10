"""Frozen cross-module contracts from design.md section 4."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import IntEnum
from types import MappingProxyType
from typing import Final


class TrafficClass(IntEnum):
    """Packet classification labels encoded in two bits."""

    UNKNOWN = 0
    HUMAN_INTERACTIVE = 1
    AGENT_INTERACTIVE = 2
    AGENT_BULK = 3


@dataclass(frozen=True)
class ClassTreatment:
    """QoS treatment associated with a traffic class."""

    dscp: int
    queue_priority: int
    meter_name: str | None
    ecn_threshold_pkts: int | None


METER_AGENT_INTERACTIVE: Final = "M_AI"
METER_AGENT_BULK: Final = "M_AB"

CLASS_TREATMENTS: Final[Mapping[TrafficClass, ClassTreatment]] = MappingProxyType(
    {
        TrafficClass.UNKNOWN: ClassTreatment(
            dscp=0,
            queue_priority=1,
            meter_name=None,
            ecn_threshold_pkts=None,
        ),
        TrafficClass.HUMAN_INTERACTIVE: ClassTreatment(
            dscp=34,
            queue_priority=2,
            meter_name=None,
            ecn_threshold_pkts=48,
        ),
        TrafficClass.AGENT_INTERACTIVE: ClassTreatment(
            dscp=18,
            queue_priority=1,
            meter_name=METER_AGENT_INTERACTIVE,
            ecn_threshold_pkts=24,
        ),
        TrafficClass.AGENT_BULK: ClassTreatment(
            dscp=8,
            queue_priority=0,
            meter_name=METER_AGENT_BULK,
            ecn_threshold_pkts=8,
        ),
    }
)

TREE_TABLE_NAMES: Final = tuple(f"tbl_tree_l{level}" for level in range(8))
FLOW_OVERRIDE_TABLE: Final = "tbl_flow_override"
CLASS_ACTION_TABLE: Final = "tbl_class_action"
PUNT_FILTER_TABLE: Final = "tbl_punt_filter"
TABLE_NAMES: Final = TREE_TABLE_NAMES + (
    FLOW_OVERRIDE_TABLE,
    CLASS_ACTION_TABLE,
    PUNT_FILTER_TABLE,
)

FLOW_KEY_REGISTER: Final = "reg_flow_key"
LAST_TIMESTAMP_REGISTER: Final = "reg_last_ts"
IAT_EWMA_REGISTER: Final = "reg_iat_ewma"
IAT_VARIANCE_EWMA_REGISTER: Final = "reg_iat_var_ewma"
PACKET_COUNT_REGISTER: Final = "reg_pkt_count"
BYTES_UP_REGISTER: Final = "reg_bytes_up"
BYTES_DOWN_REGISTER: Final = "reg_bytes_down"
FIRST_SIZES_REGISTER: Final = "reg_first_sizes"
FLOW_CLASS_REGISTER: Final = "reg_flow_class"
CM_SKETCH_REGISTERS: Final = ("reg_cm_sketch_0", "reg_cm_sketch_1")
EPOCH_FLAG_REGISTER: Final = "reg_epoch_flag"
CONGESTION_FLAG_REGISTER: Final = "reg_congestion_flag"
PROTOCOL_METADATA_REGISTER: Final = "reg_proto_meta"
POLICY_VERSION_REGISTER: Final = "policy_version"
COLLISION_COUNTER_REGISTER: Final = "reg_collision_ctr"
REGISTER_NAMES: Final = (
    FLOW_KEY_REGISTER,
    LAST_TIMESTAMP_REGISTER,
    IAT_EWMA_REGISTER,
    IAT_VARIANCE_EWMA_REGISTER,
    PACKET_COUNT_REGISTER,
    BYTES_UP_REGISTER,
    BYTES_DOWN_REGISTER,
    FIRST_SIZES_REGISTER,
    FLOW_CLASS_REGISTER,
    *CM_SKETCH_REGISTERS,
    EPOCH_FLAG_REGISTER,
    CONGESTION_FLAG_REGISTER,
    PROTOCOL_METADATA_REGISTER,
    POLICY_VERSION_REGISTER,
    COLLISION_COUNTER_REGISTER,
)

FLOW_SLOTS: Final = 65_536
CM_SKETCH_ROWS: Final = 4
CM_SKETCH_COLUMNS: Final = 4_096
FIRST_SIZE_COUNT: Final = 8
WARMUP_PACKETS: Final = 6
FEATURE_CHECKPOINTS: Final = (6, 16, 64)
IDLE_TIMEOUT_US: Final = 5_000_000
EWMA_SHIFT: Final = 3
CONGESTION_HIGH_PACKETS: Final = 64
CONGESTION_LOW_PACKETS: Final = 16

FEATURE_ORDER: Final = (
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
FEATURE_BIT_WIDTHS: Final = (32, 32, 32, 16, 16, 8, 16, 8, 8, 32)

CPU_HEADER_FIELDS: Final = (
    ("flow_hash", 16),
    ("class", 8),
    ("reason", 8),
    ("ingress_port", 16),
    ("pad", 16),
)
PUNT_REASON_FIRST_PACKET: Final = 1
PUNT_REASON_LOW_CONFIDENCE: Final = 2
PUNT_REASON_RESERVED: Final = 3

LABEL_FIELDS: Final = (
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
TRAINING_LABELS: Final = (
    TrafficClass.HUMAN_INTERACTIVE,
    TrafficClass.AGENT_INTERACTIVE,
    TrafficClass.AGENT_BULK,
)

COMPILED_TREE_FIELDS: Final = ("model_hash", "feature_order", "entries")
COMPILED_TREE_ENTRY_FIELDS: Final = (
    "table",
    "match_ranges",
    "action",
    "action_params",
    "priority",
)

EXPERIMENT_CONFIG_FIELDS: Final = (
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
EXPERIMENT_SYSTEMS: Final = ("ours", "fifo", "diffserv", "fairq", "app_limiter")
EXPERIMENT_TOPOLOGY: Final = "choke_v1"
BURST_INTENSITIES: Final = ("low", "med", "high")
MIN_LINK_MBPS: Final = 10
MAX_LINK_MBPS: Final = 50
MIN_AGENT_SHARE_PCT: Final = 10
MAX_AGENT_SHARE_PCT: Final = 90
