"""Tests for the deterministic centerpiece storm scenario."""

import json
from pathlib import Path

from harness.demo import StormConfig, build_storm_plan, write_storm_plan


def _config(tmp_path: Path, system: str = "ours") -> StormConfig:
    task_script = tmp_path / "storm_tasks.json"
    task_script.write_text(
        json.dumps([{"tool": "compute", "arguments": {"expression": "1+1"}}]),
        encoding="utf-8",
    )
    return StormConfig(
        system=system,
        link_mbps=20,
        agent_share_pct=70,
        burst_intensity="high",
        duration_s=30,
        seed=9,
        output_dir=tmp_path / "run",
        task_script=task_script,
    )


def test_storm_plan_starts_services_then_human_then_agent_burst(tmp_path: Path) -> None:
    plan = build_storm_plan(_config(tmp_path))

    assert [phase.name for phase in plan.phases] == [
        "services",
        "human-warmup",
        "agent-storm",
    ]
    assert "harness.mcp_target.server" in plan.phases[0].commands[0]
    assert plan.phases[1].commands[0][0] == "iperf3"
    assert [command[2] for command in plan.phases[2].commands] == [
        "harness.agents.browser_use",
        "harness.agents.playwright_agent",
        "harness.agents.autogen",
        "harness.agents.claude_mcp",
    ]
    assert all("9" in command for command in plan.phases[2].commands)


def test_system_selection_is_explicit_in_demo_plan(tmp_path: Path) -> None:
    assert build_storm_plan(_config(tmp_path, "ours")).protection_mode == "system-on"
    assert build_storm_plan(_config(tmp_path, "fifo")).protection_mode == "system-off"


def test_storm_plan_manifest_is_deterministic(tmp_path: Path) -> None:
    output_path = tmp_path / "plan.json"
    plan = build_storm_plan(_config(tmp_path))

    write_storm_plan(plan, output_path)
    first = output_path.read_text(encoding="utf-8")
    write_storm_plan(plan, output_path)

    assert output_path.read_text(encoding="utf-8") == first
    assert json.loads(first)["burst_intensity"] == "high"
