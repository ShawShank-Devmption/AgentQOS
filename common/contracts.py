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
FIRST_TIMESTAMP_REGISTER: Final = "reg_first_ts"
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
    FIRST_TIMESTAMP_REGISTER,
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

# --- P2.4 feature arithmetic (docs/feature_arithmetic.md) ---------------------------------


class AlpnClass(IntEnum):
    """Encoding of feature 8, `tls_alpn_class` (first ALPN protocol offered)."""

    NONE = 0
    H1 = 1
    H2 = 2
    OTHER = 3


TIMESTAMP_BITS: Final = 48
FIRST_SIZE_LARGE_BYTES: Final = 128
UPDOWN_RATIO_SCALE: Final = 100
FLOW_AGE_SHIFT: Final = 10
TLS_MAX_EXTENSIONS: Final = 16
CM_SKETCH_COUNTER_BITS: Final = 16
PROTO_META_EXT_COUNT_SHIFT: Final = 8
FLOW_KEY_SLOT_SALT: Final = 0
FLOW_KEY_TAG_SALT: Final = 1
TIMING_BURST_FEATURES: Final = ("iat_ewma_us", "iat_var_ewma", "fanout_new_flows")

# --- P4 table/action signatures the controller writes (design.md section 4.2) --------------
TREE_NODE_FIELD: Final = "tree_node"
TREE_NODE_BITS: Final = 16
TREE_KEY_FIELDS: Final = (TREE_NODE_FIELD, *FEATURE_ORDER)
TREE_NEXT_ACTION: Final = "tree_next"
TREE_LEAF_ACTION: Final = "tree_leaf"
CLASS_ACTION_NAME: Final = "set_class_action"
PUNT_ACTION: Final = "punt"
METER_SELECT: Final[Mapping[str | None, int]] = MappingProxyType(
    {None: 0, METER_AGENT_INTERACTIVE: 1, METER_AGENT_BULK: 2}
)

# --- Meter presets, indexed by reg_congestion_flag (design.md sections 5.5, 6.5) -------------
PRESET_NORMAL: Final = 0
PRESET_PROTECTIVE: Final = 1


@dataclass(frozen=True)
class MeterShare:
    """Two-rate meter rates as percentages of the bottleneck link."""

    cir_pct: int
    pir_pct: int


METER_PRESETS: Final[Mapping[int, Mapping[str, MeterShare]]] = MappingProxyType(
    {
        PRESET_NORMAL: MappingProxyType(
            {
                METER_AGENT_INTERACTIVE: MeterShare(cir_pct=50, pir_pct=80),
                METER_AGENT_BULK: MeterShare(cir_pct=30, pir_pct=60),
            }
        ),
        PRESET_PROTECTIVE: MappingProxyType(
            {
                METER_AGENT_INTERACTIVE: MeterShare(cir_pct=20, pir_pct=25),
                METER_AGENT_BULK: MeterShare(cir_pct=10, pir_pct=15),
            }
        ),
    }
)
METER_BURST_BYTES: Final = 15_000
