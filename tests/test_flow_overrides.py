"""Current-handle reconciliation and retry safety for exact-flow refinement (§7.5)."""

from unittest.mock import call, patch

import pytest

from common.contracts import FLOW_OVERRIDE_TABLE, TrafficClass
from controller.switch_api import SwitchApi, SwitchApiError

KEY = (0x0A000001, 0x0A000064, 6, 40000, 443)
EMPTY = "==========\nTABLE ENTRIES\n==========\n"
ENTRY = (
    EMPTY
    + """Dumping entry 0x9
Match key:
* meta.client_ip : EXACT 0a000001
* meta.server_ip : EXACT 0a000064
* meta.proto : EXACT 06
* meta.client_port : EXACT 9c40
* meta.server_port : EXACT 01bb
Action entry: IngressPipeline.set_flow_class - 02
"""
)


def test_upsert_discovers_current_handle_and_modifies_existing_entry() -> None:
    api = SwitchApi()
    with patch.object(api, "execute", side_effect=[(ENTRY,), ("",)]) as execute:
        api.apply_flow_overrides({KEY: TrafficClass.AGENT_BULK})
    assert execute.call_args_list == [
        call([f"table_dump {FLOW_OVERRIDE_TABLE}"]),
        call([f"table_modify {FLOW_OVERRIDE_TABLE} set_flow_class 9 3"]),
    ]


def test_upsert_missing_entry_then_retry_after_uncertain_write_uses_current_state() -> None:
    api = SwitchApi()
    with patch.object(
        api, "execute", side_effect=[(EMPTY,), SwitchApiError("partial batch"), (ENTRY,)]
    ) as execute:
        with pytest.raises(SwitchApiError, match="partial"):
            api.apply_flow_overrides({KEY: TrafficClass.AGENT_INTERACTIVE})
        api.apply_flow_overrides({KEY: TrafficClass.AGENT_INTERACTIVE})
    assert execute.call_args_list == [
        call([f"table_dump {FLOW_OVERRIDE_TABLE}"]),
        call(
            [f"table_add {FLOW_OVERRIDE_TABLE} set_flow_class 167772161 167772260 6 40000 443 => 2"]
        ),
        call([f"table_dump {FLOW_OVERRIDE_TABLE}"]),
    ]


def test_delete_is_idempotent_and_reconciles_reallocated_handles() -> None:
    api = SwitchApi()
    with patch.object(api, "execute", side_effect=[(ENTRY,), ("",), (EMPTY,)]) as execute:
        api.apply_flow_overrides({KEY: None})
        api.apply_flow_overrides({KEY: None})
    assert execute.call_args_list == [
        call([f"table_dump {FLOW_OVERRIDE_TABLE}"]),
        call([f"table_delete {FLOW_OVERRIDE_TABLE} 9"]),
        call([f"table_dump {FLOW_OVERRIDE_TABLE}"]),
    ]


@pytest.mark.parametrize(
    "transcript",
    [
        "",
        "garbage",
        ENTRY.replace("EXACT 06", "TERNARY 06"),
        ENTRY.replace("set_flow_class - 02", "unknown_action - 02"),
        ENTRY.replace("set_flow_class - 02", "set_flow_class - 08"),
        ENTRY + ENTRY,
    ],
)
def test_malformed_dump_never_becomes_an_empty_table(transcript: str) -> None:
    api = SwitchApi()
    with (
        patch.object(api, "execute", return_value=(transcript,)) as execute,
        pytest.raises(SwitchApiError),
    ):
        api.apply_flow_overrides({KEY: TrafficClass.AGENT_INTERACTIVE})
    execute.assert_called_once_with([f"table_dump {FLOW_OVERRIDE_TABLE}"])


@pytest.mark.parametrize(
    "key", [(1, 2, 6, -1, 443), (1, 2, 6, 65536, 443), (1, 2, 6), (True, 2, 6, 4, 5)]
)
def test_invalid_flow_keys_fail_before_switch_io(key: tuple[int, ...]) -> None:
    api = SwitchApi()
    with patch.object(api, "execute") as execute, pytest.raises(ValueError):
        api.apply_flow_overrides({key: TrafficClass.AGENT_INTERACTIVE})
    execute.assert_not_called()


def test_empty_change_set_does_not_read_the_switch() -> None:
    api = SwitchApi()
    with patch.object(api, "execute") as execute:
        api.apply_flow_overrides({})
    execute.assert_not_called()
