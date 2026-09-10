"""Unit tests for the BMv2 CLI trust boundary."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from common.contracts import POLICY_VERSION_REGISTER
from controller.switch_api import SwitchApi, SwitchApiError, _parse_register_output


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
        stdout="policy_version[0]= 7\n",
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
        SwitchApi(Path("simple_switch_CLI")).read_register(
            POLICY_VERSION_REGISTER,
            index=0,
        )
