"""Unit tests for the BMv2 CLI trust boundary."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from common.contracts import POLICY_VERSION_REGISTER
from controller.switch_api import SwitchApi, SwitchApiError, _parse_register_output

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
