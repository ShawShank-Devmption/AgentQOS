"""Startup policy push: contents, idempotence, crash/restart safety (design.md 6.1, 7.5)."""

import pytest

from common.compiled_tree import parse_compiled_tree
from common.contracts import (
    CLASS_ACTION_TABLE,
    FEATURE_BIT_WIDTHS,
    FEATURE_ORDER,
    FLOW_OVERRIDE_TABLE,
    METER_AGENT_BULK,
    METER_AGENT_INTERACTIVE,
    POLICY_VERSION_REGISTER,
    PRESET_PROTECTIVE,
)
from controller.policy import link_share_bytes_per_us, policy_commands, push_policy
from controller.switch_api import SwitchApiError
from tests.fake_bmv2 import FakeBmv2

TREE = parse_compiled_tree(
    {
        "model_hash": "h1",
        "feature_order": list(FEATURE_ORDER),
        "entries": [
            {
                "table": "tbl_tree_l0",
                "match_ranges": [[0, 0]] + [[0, (1 << b) - 1] for b in FEATURE_BIT_WIDTHS],
                "action": "tree_leaf",
                "action_params": [1, 0],
                "priority": 1,
            }
        ],
    }
)


def test_push_installs_class_actions_meters_tree_and_bumps_version() -> None:
    switch = FakeBmv2()
    assert push_policy(switch, link_mbps=20, tree=TREE) == 1
    assert switch.tables[CLASS_ACTION_TABLE]["1"].endswith("=> 34 2 0 48")  # §4.1 HUMAN
    assert switch.tables[CLASS_ACTION_TABLE]["0"].endswith("=> 0 1 0 0")  # UNKNOWN: no ECN
    assert len(switch.tables["tbl_tree_l0"]) == 1
    assert switch.tables[FLOW_OVERRIDE_TABLE] == {}
    assert switch.meters[(METER_AGENT_INTERACTIVE, PRESET_PROTECTIVE)] == (
        "0.500000:15000 0.625000:15000"
    )
    assert switch.registers[(POLICY_VERSION_REGISTER, 0)] == 1


def test_restart_repush_is_idempotent_and_version_is_monotonic() -> None:
    switch = FakeBmv2()
    push_policy(switch, 20, TREE)
    first = {name: dict(entries) for name, entries in switch.tables.items()}
    assert push_policy(switch, 20, TREE) == 2  # §7.5: no DUPLICATE_ENTRY on re-push
    assert switch.tables == first


def test_crash_mid_push_leaves_old_version_and_next_start_recovers() -> None:
    switch = FakeBmv2()
    push_policy(switch, 20, TREE)
    switch.crash_after = switch.executed + 15
    with pytest.raises(SwitchApiError, match="crash"):
        push_policy(switch, 20, TREE)
    assert switch.registers[(POLICY_VERSION_REGISTER, 0)] == 1  # version written last
    switch.crash_after = None
    assert push_policy(switch, 20, TREE) == 2


def test_push_without_tree_leaves_tree_tables_empty_fail_open() -> None:
    switch = FakeBmv2()
    push_policy(switch, 20, None)
    assert switch.tables["tbl_tree_l0"] == {}


def test_link_share_and_link_bounds() -> None:
    assert link_share_bytes_per_us(20, 20) == 0.5
    assert link_share_bytes_per_us(50, 100) == 6.25
    with pytest.raises(ValueError):
        policy_commands(5, None)


def test_bulk_meter_presets_both_installed() -> None:
    switch = FakeBmv2()
    push_policy(switch, 20, None)
    assert {key[0] for key in switch.meters} == {METER_AGENT_INTERACTIVE, METER_AGENT_BULK}
    assert {key[1] for key in switch.meters} == {0, 1}


def test_class_action_default_is_unknown_treatment_before_table_is_cleared() -> None:
    # During the clear-then-add window every packet hits the default action; it must be the
    # UNKNOWN treatment (fail-open), not whatever default the P4 program declares.
    commands = policy_commands(20, None)
    default = "table_set_default tbl_class_action set_class_action 0 1 0 0"
    assert commands.index(default) < commands.index("table_clear tbl_class_action")
    switch = FakeBmv2()
    push_policy(switch, 20, None)
    assert switch.defaults[CLASS_ACTION_TABLE] == "set_class_action 0 1 0 0"
