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


def _run_vm_with_fake_gcloud(tmp_path: Path, *args: str) -> list[str]:
    log = tmp_path / "gcloud.args"
    fake = tmp_path / "gcloud"
    fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@" >"$AAN_FAKE_LOG"\ncat >"$AAN_FAKE_LOG.stdin"\n')
    fake.chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "AAN_FAKE_LOG": str(log)}
    subprocess.run(
        ["bash", str(GATEWAY / "vm.sh"), *args], env=env, check=True, stdin=subprocess.DEVNULL
    )
    return log.read_text().splitlines()


def test_vm_ssh_forwards_arguments(tmp_path: Path) -> None:
    argv = _run_vm_with_fake_gcloud(tmp_path, "ssh", "--command", "uptime")
    assert argv[:2] == ["compute", "ssh"]
    assert "--tunnel-through-iap" in argv
    assert argv[-2:] == ["--command", "uptime"]


def test_vm_tunnel_keeps_gcloud_flags_before_ssh_args(tmp_path: Path) -> None:
    argv = _run_vm_with_fake_gcloud(tmp_path, "tunnel")
    separator = argv.index("--")
    assert all(flag in argv[:separator] for flag in ("--tunnel-through-iap", "--quiet"))
    assert any(arg.startswith("--zone=") for arg in argv[:separator])
    assert argv[separator + 1 :] == ["-N", "-L", "8080:localhost:8080"]


def test_vm_helper_rejects_unsafe_device_name() -> None:
    result = subprocess.run(
        ["bash", str(GATEWAY / "vm.sh"), "add-device", "x; rm -rf /"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "invalid device name" in result.stderr


def test_vm_capture_start_sends_validated_session_to_vm(tmp_path: Path) -> None:
    argv = _run_vm_with_fake_gcloud(
        tmp_path, "capture-start", "call-1", "shashank-phone", "HUMAN_INTERACTIVE", "video-call"
    )
    assert argv[argv.index("--command") + 1] == (
        "bash -s -- call-1 shashank-phone HUMAN_INTERACTIVE video-call gw-lan-sw"
    )
    remote = (tmp_path / "gcloud.args.stdin").read_text()
    assert "/etc/aan-gateway/peers/$device.conf" in remote
    assert 'python -m harness.capture start "results/corpus/$session"' in remote


@pytest.mark.parametrize(
    "args",
    [
        ("call-1", "shashank-phone", "UNKNOWN", "video-call"),
        ("../x", "shashank-phone", "AGENT_BULK", "crawl"),
        ("call-1", "shashank-phone", "AGENT_BULK", "a;b"),
        ("call-1", "", "AGENT_BULK", "crawl"),
    ],
)
def test_vm_capture_start_rejects_unsafe_arguments(tmp_path: Path, args: tuple[str, ...]) -> None:
    log = tmp_path / "gcloud.args"
    fake = tmp_path / "gcloud"
    fake.write_text('#!/bin/sh\ntouch "$AAN_FAKE_LOG"\n')
    fake.chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "AAN_FAKE_LOG": str(log)}
    result = subprocess.run(
        ["bash", str(GATEWAY / "vm.sh"), "capture-start", *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode == 2
    assert not log.exists()


def test_vm_start_waits_until_ssh_answers(tmp_path: Path) -> None:
    argv = _run_vm_with_fake_gcloud(tmp_path, "start")
    # The fake succeeds at once, so the last recorded call is the SSH readiness probe.
    assert argv[:2] == ["compute", "ssh"]
    assert argv[-2:] == ["--command", "true"]


def test_g2_switch_handover_never_runs_network_setup(tmp_path: Path) -> None:
    script = tmp_path / "netsetup.sh"
    script.write_text((GATEWAY / "netsetup.sh").read_text())
    (tmp_path / "gateway.env").write_text("")
    (tmp_path / "bypass.sh").write_text('#!/bin/sh\nprintf "%s\\n" "$@" >"$AAN_FAKE_LOG"\n')
    (tmp_path / "bypass.sh").chmod(0o755)
    log = tmp_path / "handover.args"
    result = subprocess.run(
        ["bash", str(script), "--switch"],
        env={**os.environ, "AAN_FAKE_LOG": str(log)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert log.read_text() == "off\n"


def test_g2_default_bypass_restores_on_without_recreating_interfaces(tmp_path: Path) -> None:
    script = tmp_path / "bypass.sh"
    script.write_text((GATEWAY / "bypass.sh").read_text())
    (tmp_path / "gateway.env").write_text(
        "AAN_LAN_SW=gw-lan-sw\nAAN_WAN_SW=gw-wan-sw\nAAN_BYPASS_BR=br-bypass\n"
    )
    tools = tmp_path / "bin"
    tools.mkdir()
    (tools / "id").write_text('#!/bin/sh\nprintf "0\\n"\n')
    (tools / "id").chmod(0o755)
    (tools / "ip").write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >>"$AAN_FAKE_LOG"\n'
        'if [ "$1" = -o ]; then printf "master br-bypass\\n"; fi\n'
    )
    (tools / "ip").chmod(0o755)
    log = tmp_path / "ip.args"
    result = subprocess.run(
        ["bash", str(script)],
        env={**os.environ, "PATH": f"{tools}:{os.environ['PATH']}", "AAN_FAKE_LOG": str(log)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    commands = log.read_text().splitlines()
    assert "link set gw-lan-sw master br-bypass" in commands
    assert "link set gw-wan-sw master br-bypass" in commands
    assert not any("add" in command or "del" in command for command in commands)
