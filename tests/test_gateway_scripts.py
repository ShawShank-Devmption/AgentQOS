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
        "bash -s -- call-1 shashank-phone HUMAN_INTERACTIVE video-call gw-lan-sw 0"
    )
    remote = (tmp_path / "gcloud.args.stdin").read_text()
    assert "/etc/aan-gateway/peers/$device.conf" in remote
    assert '"${python[@]}" start "results/corpus/$session"' in remote
    assert "systemd-run" in remote  # present but only runs when seconds > 0


@pytest.mark.parametrize(
    "args",
    [
        ("call-1", "shashank-phone", "UNKNOWN", "video-call"),
        ("../x", "shashank-phone", "AGENT_BULK", "crawl"),
        ("call-1", "shashank-phone", "AGENT_BULK", "a;b"),
        ("call-1", "", "AGENT_BULK", "crawl"),
        ("call-1", "team-laptop", "AGENT_BULK", "crawl", "10"),
        ("call-1", "team-laptop", "AGENT_BULK", "crawl", "5m"),
        ("call-1", "team-laptop", "AGENT_BULK", "crawl", "9999"),
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


def test_vm_timed_capture_lets_the_vm_close_the_window(tmp_path: Path) -> None:
    argv = _run_vm_with_fake_gcloud(
        tmp_path, "capture-start", "agent-1", "team-laptop", "AGENT_INTERACTIVE", "claude", "300"
    )
    assert argv[argv.index("--command") + 1].endswith("gw-lan-sw 300")
    remote = (tmp_path / "gcloud.args.stdin").read_text()
    assert '--unit "aan-capture-$session" --on-active="${seconds}s"' in remote
    assert 'stop "results/corpus/$session"' in remote
