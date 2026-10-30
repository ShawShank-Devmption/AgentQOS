"""Static checks for the live-gateway operations scripts (docs/demo_tasks.md I2-I4).

Runtime behaviour is verified on the VM by harness/gateway/selftest.sh; these tests keep the
scripts parseable, strict and executable on every host.
"""

import os
import subprocess
from pathlib import Path

import pytest

GATEWAY = Path(__file__).resolve().parents[1] / "harness" / "gateway"
SCRIPTS = sorted(GATEWAY.glob("*.sh"))
INSTALLED = ("netsetup", "teardown", "bypass", "add_device", "revoke_device", "selftest")


def test_installer_ships_every_vm_script() -> None:
    names = {script.stem for script in SCRIPTS}
    assert set(INSTALLED) <= names
    installer = (GATEWAY / "install.sh").read_text()
    assert f"for script in {' '.join(INSTALLED)}; do" in installer


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda path: path.name)
def test_script_parses_strict_and_executable(script: Path) -> None:
    subprocess.run(["bash", "-n", str(script)], check=True)
    assert "set -euo pipefail" in script.read_text()
    assert os.access(script, os.X_OK)


def test_vm_helper_without_command_prints_usage() -> None:
    result = subprocess.run(
        ["bash", str(GATEWAY / "vm.sh")], capture_output=True, text=True, check=False
    )
    assert result.returncode == 2
    assert "usage: harness/gateway/vm.sh" in result.stderr


def test_vm_helper_rejects_unsafe_device_name() -> None:
    result = subprocess.run(
        ["bash", str(GATEWAY / "vm.sh"), "add-device", "x; rm -rf /"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "invalid device name" in result.stderr
