"""Unit tests for the BMv2 CLI trust boundary."""

import fcntl
import subprocess
from pathlib import Path
from unittest.mock import ANY, patch

import pytest

from common.contracts import CLASS_ACTION_TABLE, METER_AGENT_INTERACTIVE, POLICY_VERSION_REGISTER
from controller.switch_api import (
    SwitchApi,
    SwitchApiError,
    _lock_path,
    _parse_register_output,
    meter_set_rates_command,
    register_write_command,
    table_add_command,
    table_clear_command,
)

BANNER = "Obtaining JSON from switch...\nDone\nControl utility for runtime P4 table manipulation\n"


def _cli(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def test_parse_scalar_register_output() -> None:
    output = "RuntimeCmd: register_read policy_version 0\npolicy_version[0]= 0x2a\n"
    assert _parse_register_output(POLICY_VERSION_REGISTER, output) == (42,)


def test_parse_array_register_output() -> None:
    output = "RuntimeCmd: register_read reg_pkt_count\nreg_pkt_count= 0, 7, 11\n"
    assert _parse_register_output("reg_pkt_count", output) == (0, 7, 11)


def test_parse_rejects_missing_register() -> None:
    with pytest.raises(SwitchApiError, match="did not contain"):
        _parse_register_output(POLICY_VERSION_REGISTER, "RuntimeCmd: register_read\n")


def test_read_register_rejects_unknown_name() -> None:
    with pytest.raises(ValueError, match="unknown contract register"):
        SwitchApi().read_register("invented_register")


def test_read_register_invokes_cli_with_validated_command() -> None:
    succeeded = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout=BANNER + "RuntimeCmd: policy_version[0]= 7\nRuntimeCmd: ",
        stderr="",
    )
    with patch("controller.switch_api.subprocess.run", return_value=succeeded) as run:
        values = SwitchApi(Path("simple_switch_CLI"), thrift_port=9091).read_register(
            POLICY_VERSION_REGISTER,
            index=0,
        )

    assert values == (7,)
    run.assert_called_once_with(
        ["simple_switch_CLI", "--thrift-port", "9091"],
        input="register_read policy_version 0\n",
        capture_output=True,
        check=False,
        text=True,
        pass_fds=ANY,
    )


def test_read_register_surfaces_cli_failure() -> None:
    failed = subprocess.CompletedProcess(
        args=[],
        returncode=1,
        stdout="",
        stderr="Could not connect to thrift server",
    )
    with (
        patch("controller.switch_api.subprocess.run", return_value=failed),
        pytest.raises(SwitchApiError, match="Could not connect"),
    ):
        SwitchApi(Path("simple_switch_CLI")).read_register(POLICY_VERSION_REGISTER, index=0)


def test_execute_runs_one_session_and_splits_outputs() -> None:
    stdout = BANNER + "RuntimeCmd: out one\nRuntimeCmd: out two\nRuntimeCmd: "
    with patch("controller.switch_api.subprocess.run", return_value=_cli(stdout)) as run:
        outputs = SwitchApi().execute(["cmd_a", "cmd_b"])
    assert outputs == ("out one\n", "out two\n")
    assert run.call_args.kwargs["input"] == "cmd_a\ncmd_b\n"
    run.assert_called_once()


@pytest.mark.parametrize(
    "rejection",
    [
        "Invalid table operation (DUPLICATE_ENTRY)",
        "Error: Invalid register name",
        "*** Unknown syntax: table_ad x",
        "Invalid match key: Expected 11 key fields",
        "Invalid runtime data: Parameter class is too wide",
    ],
)
def test_execute_raises_when_cli_rejects_a_command_despite_exit_zero(rejection: str) -> None:
    stdout = BANNER + "RuntimeCmd: ok\nRuntimeCmd: " + rejection + "\nRuntimeCmd: "
    with (
        patch("controller.switch_api.subprocess.run", return_value=_cli(stdout)),
        pytest.raises(SwitchApiError, match="cmd_b"),
    ):
        SwitchApi().execute(["cmd_a", "cmd_b"])


def test_execute_raises_on_short_transcript() -> None:
    with (
        patch("controller.switch_api.subprocess.run", return_value=_cli(BANNER)),
        pytest.raises(SwitchApiError, match="transcript"),
    ):
        SwitchApi().execute(["cmd_a"])


def test_execute_rejects_multiline_commands() -> None:
    with pytest.raises(ValueError, match="single line"):
        SwitchApi().execute(["register_reset reg_pkt_count\ntable_clear tbl_class_action"])


def test_read_registers_reads_many_arrays_in_one_session() -> None:
    stdout = BANNER + "RuntimeCmd: reg_pkt_count= 0, 7\nRuntimeCmd: reg_last_ts= 5, 9\nRuntimeCmd: "
    with patch("controller.switch_api.subprocess.run", return_value=_cli(stdout)) as run:
        values = SwitchApi().read_registers(["reg_pkt_count", "reg_last_ts"])
    assert values == {"reg_pkt_count": (0, 7), "reg_last_ts": (5, 9)}
    run.assert_called_once()


def test_read_registers_rejects_unknown_name_before_running_cli() -> None:
    with (
        patch("controller.switch_api.subprocess.run") as run,
        pytest.raises(ValueError, match="unknown contract register"),
    ):
        SwitchApi().read_registers(["reg_pkt_count", "invented"])
    run.assert_not_called()


def test_table_add_command_exact_and_range_keys_with_priority() -> None:
    command = table_add_command("tbl_tree_l0", "tree_next", [3, (0, 12), (5, 5)], [7], 1)
    assert command == "table_add tbl_tree_l0 tree_next 3 0->12 5->5 => 7 1"


def test_table_add_command_without_params() -> None:
    assert table_add_command("tbl_punt_filter", "punt", [1], []) == (
        "table_add tbl_punt_filter punt 1 =>"
    )


@pytest.mark.parametrize(
    ("table", "keys", "params"),
    [
        ("tbl_invented", [1], []),
        (CLASS_ACTION_TABLE, [(5, 4)], []),
        (CLASS_ACTION_TABLE, [1], [-1]),
    ],
)
def test_table_add_command_rejects_invalid_input(table: str, keys: list, params: list) -> None:
    with pytest.raises(ValueError):
        table_add_command(table, "set_class_action", keys, params)


def test_clear_register_and_meter_commands() -> None:
    assert table_clear_command(CLASS_ACTION_TABLE) == "table_clear tbl_class_action"
    assert register_write_command("policy_version", 0, 4) == "register_write policy_version 0 4"
    assert meter_set_rates_command(METER_AGENT_INTERACTIVE, 1, [(0.5, 15000), (0.625, 15000)]) == (
        "meter_set_rates M_AI 1 0.500000:15000 0.625000:15000"
    )
    with pytest.raises(ValueError):
        register_write_command("invented", 0, 1)
    with pytest.raises(ValueError):
        meter_set_rates_command("M_X", 0, [(1.0, 1)])


def test_execute_holds_switch_lock_that_the_cli_child_inherits() -> None:
    # §7.5: a SIGKILLed controller leaves its CLI child running the rest of the batch; the child
    # keeps the lock so a restarted controller waits instead of interleaving its re-push.
    held = {}

    def fake_run(*_args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        held["pass_fds"] = kwargs["pass_fds"]
        with _lock_path(9095).open("a") as other:
            try:
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
                held["locked"] = False
            except BlockingIOError:
                held["locked"] = True
        return _cli(BANNER + "RuntimeCmd: ok\nRuntimeCmd: ")

    with patch("controller.switch_api.subprocess.run", side_effect=fake_run):
        SwitchApi(thrift_port=9095).execute(["cmd_a"])
    assert held["locked"] is True
    assert len(held["pass_fds"]) == 1
    with _lock_path(9095).open("a") as after:
        fcntl.flock(after, fcntl.LOCK_EX | fcntl.LOCK_NB)  # released once the session ends
