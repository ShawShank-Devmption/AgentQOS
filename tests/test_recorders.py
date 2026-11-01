"""Recorder scripts (record_interactive.sh, record_bulk.sh) run against fake gcloud/claude/macOS.

The VM side (attached_capture.sh) is exercised on the VM itself; these tests pin the Mac-side
order of operations: checks, quitting apps, opening the window, Claude, then the stop.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

GATEWAY = Path(__file__).resolve().parents[1] / "harness" / "gateway"
PROMPTS = Path(__file__).resolve().parents[1] / "harness" / "agents" / "prompts"
VM_IP = "203.0.113.7"
TUNNEL_IP = "10.42.0.12"

FAKES = {
    "gcloud": f"""#!/bin/sh
echo "gcloud $*" >>"$AAN_FAKE_LOG"
case "$*" in
*"instances describe"*) printf 'RUNNING\\t{VM_IP}\\n' ;;
*"AllowedIPs = {TUNNEL_IP}/32"*) echo ananya-mac ;;
*AllowedIPs*) echo "no device has that address" >&2; exit 1 ;;
*attached_capture.sh*)
	echo AAN_READY
	read -r line
	echo "remote-got:$line" >>"$AAN_FAKE_LOG"
	printf '{{\\n  "status": "complete"\\n}}\\n'
	;;
esac
""",
    "ifconfig": """#!/bin/sh
printf 'en0: flags=8863<UP>\\n\\tinet 192.168.1.20 netmask 0xffffff00\\n'
[ -n "$AAN_FAKE_TUNNEL" ] && printf 'utun4: flags=8051<UP>\\n\\tinet %s --> %s\\n' \\
	"$AAN_FAKE_TUNNEL" "$AAN_FAKE_TUNNEL"
exit 0
""",
    "curl": f"""#!/bin/sh
printf 'fl=1\\nip=%s\\n' "${{AAN_FAKE_EGRESS:-{VM_IP}}}"
""",
    "osascript": """#!/bin/sh
case "$*" in
*"to quit"*)
	echo "osascript $*" >>"$AAN_FAKE_LOG"
	touch "$AAN_FAKE_LOG.quit"
	;;
*)
	if [ -e "$AAN_FAKE_LOG.quit" ]; then echo "com.apple.Terminal"
	else echo "com.apple.Terminal, com.microsoft.VSCode, com.wireguard.macos"; fi
	;;
esac
""",
    "claude": """#!/bin/sh
{ printf 'claude cwd=%s' "$PWD"; for arg in "$@"; do printf ' [%s]' "$arg"; done; echo; } \\
	>>"$AAN_FAKE_LOG"
""",
}


@pytest.fixture
def fake_env(tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in FAKES.items():
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    return {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "AAN_FAKE_LOG": str(tmp_path / "calls.log"),
        "AAN_SESSIONS_DIR": str(tmp_path / "sessions"),
        "TERM_PROGRAM": "Apple_Terminal",
        "AAN_PYTHON": sys.executable,
        "AAN_FAKE_TUNNEL": TUNNEL_IP,
    }


def _record(
    env: dict[str, str], *args: str, script: str = "record_interactive.sh"
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(GATEWAY / script), *args],
        env=env,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=60,
        check=False,
    )


def test_interactive_session_runs_checks_quit_window_claude_stop_in_order(
    tmp_path: Path, fake_env: dict[str, str]
) -> None:
    result = _record(fake_env, "agent-03")
    assert result.returncode == 0, result.stderr
    calls = (tmp_path / "calls.log").read_text().splitlines()
    order = [
        next(i for i, c in enumerate(calls) if marker in c)
        for marker in (
            "instances describe",
            "test ! -e ~/aan-results/corpus/agent-03",
            "com.microsoft.VSCode",
            "attached_capture.sh agent-03 ananya-mac AGENT_INTERACTIVE claude-code-interactive",
            "claude cwd=",
            "remote-got:stop",
        )
    ]
    assert order == sorted(order)
    assert not any("com.wireguard.macos" in c and "quit" in c for c in calls)
    prompt = (tmp_path / "sessions" / "agent-03" / "prompt.txt").read_text().strip()
    assert prompt in (PROMPTS / "interactive.txt").read_text().splitlines()
    assert f"[{prompt}]" in next(c for c in calls if c.startswith("claude"))
    assert '"status": "complete"' in result.stdout


def test_refuses_to_run_outside_terminal_app(tmp_path: Path, fake_env: dict[str, str]) -> None:
    result = _record({**fake_env, "TERM_PROGRAM": "vscode"}, "agent-03")
    assert result.returncode == 1
    assert "Terminal.app" in result.stderr
    assert not (tmp_path / "calls.log").exists()


def test_refuses_when_traffic_bypasses_the_gateway(
    tmp_path: Path, fake_env: dict[str, str]
) -> None:
    result = _record({**fake_env, "AAN_FAKE_EGRESS": "198.51.100.9"}, "agent-03")
    assert result.returncode == 1
    assert "turn WireGuard on" in result.stderr
    assert "attached_capture" not in (tmp_path / "calls.log").read_text()


def test_refuses_without_a_wireguard_address_on_this_mac(
    tmp_path: Path, fake_env: dict[str, str]
) -> None:
    result = _record({**fake_env, "AAN_FAKE_TUNNEL": ""}, "agent-03")
    assert result.returncode == 1
    assert "WireGuard address" in result.stderr
    assert "attached_capture" not in (tmp_path / "calls.log").read_text()


def test_refuses_when_no_device_profile_owns_the_tunnel_address(
    tmp_path: Path, fake_env: dict[str, str]
) -> None:
    # The captured device is whichever profile this Mac's tunnel uses, never a default name.
    result = _record({**fake_env, "AAN_FAKE_TUNNEL": "10.42.0.99"}, "agent-03")
    assert result.returncode == 1
    calls = (tmp_path / "calls.log").read_text()
    assert "AllowedIPs = 10.42.0.99/32" in calls
    assert "attached_capture" not in calls


@pytest.mark.parametrize("args", [(), ("../x",), ("a", "b")])
def test_rejects_bad_arguments(fake_env: dict[str, str], args: tuple[str, ...]) -> None:
    assert _record(fake_env, *args).returncode == 2


def test_same_session_name_always_gets_the_same_prompt(
    tmp_path: Path, fake_env: dict[str, str]
) -> None:
    script = f'. "{GATEWAY}/record_lib.sh"; pick_prompt interactive agent-03'
    picks = {
        subprocess.run(
            ["bash", "-c", script], env=fake_env, capture_output=True, text=True, check=True
        ).stdout
        for _ in range(3)
    }
    assert len(picks) == 1


def test_bulk_session_runs_headless_claude_limited_to_the_crawler(
    tmp_path: Path, fake_env: dict[str, str]
) -> None:
    result = _record(fake_env, "bulk-01", script="record_bulk.sh")
    assert result.returncode == 0, result.stderr
    calls = (tmp_path / "calls.log").read_text().splitlines()
    assert any(
        "attached_capture.sh bulk-01 ananya-mac AGENT_BULK claude-code-crawler" in c for c in calls
    )
    claude = next(c for c in calls if c.startswith("claude"))
    crawler = GATEWAY.parent / "agents" / "crawler.py"
    assert "[-p]" in claude
    assert f"[Bash({sys.executable} {crawler}:*)] [Read] [Write]" in claude
    prompt = (tmp_path / "sessions" / "bulk-01" / "prompt.txt").read_text()
    assert "@CRAWLER@" not in prompt
    assert f"`{sys.executable} {crawler} --crawlers " in prompt
    assert calls[-1] == "remote-got:stop"
    assert '"status": "complete"' in result.stdout


def test_bulk_requires_the_repository_python(tmp_path: Path, fake_env: dict[str, str]) -> None:
    env = {**fake_env, "AAN_PYTHON": str(tmp_path / "missing-python")}
    result = _record(env, "bulk-01", script="record_bulk.sh")
    assert result.returncode == 1
    assert "virtual environment" in result.stderr
    assert not (tmp_path / "calls.log").exists()
