"""Startup policy push: class actions, meter presets, tree entries (design.md 6.1, 6.5)."""

from __future__ import annotations

import logging

from common.compiled_tree import CompiledTree
from common.contracts import (
    CLASS_ACTION_NAME,
    CLASS_ACTION_TABLE,
    CLASS_TREATMENTS,
    FLOW_OVERRIDE_TABLE,
    MAX_LINK_MBPS,
    METER_BURST_BYTES,
    METER_PRESETS,
    METER_SELECT,
    MIN_LINK_MBPS,
    POLICY_VERSION_REGISTER,
    PUNT_ACTION,
    PUNT_FILTER_TABLE,
    PUNT_REASON_FIRST_PACKET,
    PUNT_REASON_LOW_CONFIDENCE,
    TREE_TABLE_NAMES,
)
from controller.switch_api import (
    SwitchApi,
    meter_set_rates_command,
    register_write_command,
    table_add_command,
    table_clear_command,
)

LOGGER = logging.getLogger(__name__)

# §7.5: every controller-owned table is rebuilt on each push. Overrides are cleared too: their
# TTL bookkeeping died with the previous controller process, and the tree stays authoritative.
_OWNED_TABLES = (*TREE_TABLE_NAMES, CLASS_ACTION_TABLE, PUNT_FILTER_TABLE, FLOW_OVERRIDE_TABLE)
_PUNT_REASONS = (PUNT_REASON_FIRST_PACKET, PUNT_REASON_LOW_CONFIDENCE)


def push_policy(api: SwitchApi, link_mbps: int, tree: CompiledTree | None) -> int:
    """Push the full policy, then advance policy_version.

    Safe at any time (section 7.5): owned tables are cleared before being filled, meters and
    registers are overwritten, and the version is written last, so a push cut short by a crash
    leaves the old version and the next start re-pushes over it.

    Args:
        api: Switch boundary.
        link_mbps: Bottleneck rate from the experiment config; scales the meter presets.
        tree: Compiled classifier, or None before one exists (every flow stays UNKNOWN).

    Returns:
        The policy version now stored on the switch.

    Raises:
        SwitchApiError: If BMv2 rejects any command.
        ValueError: If link_mbps is outside the contract range.
    """
    (current,) = api.read_register(POLICY_VERSION_REGISTER, 0)
    if tree is None:
        LOGGER.warning("no compiled tree; all flows classify as UNKNOWN (fail-open)")
    api.execute(policy_commands(link_mbps, tree))
    version = current + 1
    api.execute([register_write_command(POLICY_VERSION_REGISTER, 0, version)])
    LOGGER.info("pushed policy version %d (model %s)", version, tree.model_hash if tree else "-")
    return version


def policy_commands(link_mbps: int, tree: CompiledTree | None) -> list[str]:
    """Return every CLI command of a full policy push, in execution order."""
    commands = [table_clear_command(table) for table in _OWNED_TABLES]
    commands += _class_action_commands()
    commands += [table_add_command(PUNT_FILTER_TABLE, PUNT_ACTION, [r], []) for r in _PUNT_REASONS]
    commands += _meter_preset_commands(link_mbps)
    if tree is not None:
        commands += [
            table_add_command(
                entry.table,
                entry.action,
                [entry.match_ranges[0][0], *entry.match_ranges[1:]],
                entry.action_params,
                entry.priority,
            )
            for entry in tree.entries
        ]
    return commands


def link_share_bytes_per_us(link_mbps: int, pct: int) -> float:
    """Convert a link share to BMv2 byte-meter units: Mbps / 8 bytes per microsecond."""
    return link_mbps * pct / 800


def _class_action_commands() -> list[str]:
    return [
        table_add_command(
            CLASS_ACTION_TABLE,
            CLASS_ACTION_NAME,
            [int(traffic_class)],
            [
                treatment.dscp,
                treatment.queue_priority,
                METER_SELECT[treatment.meter_name],
                treatment.ecn_threshold_pkts or 0,  # 0 disables ECN marking
            ],
        )
        for traffic_class, treatment in CLASS_TREATMENTS.items()
    ]


def _meter_preset_commands(link_mbps: int) -> list[str]:
    # §5.5: both presets are installed; the data plane picks one by reg_congestion_flag.
    if not MIN_LINK_MBPS <= link_mbps <= MAX_LINK_MBPS:
        raise ValueError(f"link_mbps must be within {MIN_LINK_MBPS}..{MAX_LINK_MBPS}")
    return [
        meter_set_rates_command(
            meter,
            preset,
            [
                (link_share_bytes_per_us(link_mbps, share.cir_pct), METER_BURST_BYTES),
                (link_share_bytes_per_us(link_mbps, share.pir_pct), METER_BURST_BYTES),
            ],
        )
        for preset, shares in METER_PRESETS.items()
        for meter, share in shares.items()
    ]
