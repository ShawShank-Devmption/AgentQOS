"""Tests for the deterministic centerpiece storm scenario."""

import json
from pathlib import Path

from harness.demo import StormConfig, build_storm_plan, main, write_storm_plan


def _config(tmp_path: Path, system: str = "ours", **overrides: object) -> StormConfig:
    task_script = tmp_path / "storm_tasks.json"
    task_script.write_text(
        json.dumps([{"tool": "compute", "arguments": {"expression": "1+1"}}]),
        encoding="utf-8",
    )
    values: dict[str, object] = {
        "system": system,
        "link_mbps": 20,
        "agent_share_pct": 70,
        "burst_intensity": "high",
        "duration_s": 30,
        "seed": 9,
        "output_dir": tmp_path / "run",
        "task_script": task_script,
    }
    values.update(overrides)
    return StormConfig(**values)  # type: ignore[arg-type]


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
    assert [command[command.index("--source-ip") + 1] for command in plan.phases[2].commands] == [
        "10.0.0.1",
        "10.0.0.4",
        "10.0.0.5",
        "10.0.0.6",
    ]


def test_share_and_duration_change_agent_workload_size(tmp_path: Path) -> None:
    low_share = build_storm_plan(_config(tmp_path, agent_share_pct=10, duration_s=10))
    high_share = build_storm_plan(_config(tmp_path, agent_share_pct=90, duration_s=30))

    low_command = low_share.phases[2].commands[0]
    high_command = high_share.phases[2].commands[0]
    low_repetitions = int(low_command[low_command.index("--repetitions") + 1])
    high_repetitions = int(high_command[high_command.index("--repetitions") + 1])

    assert high_repetitions > low_repetitions


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


def test_execute_mode_fails_instead_of_reporting_a_plan_as_complete(tmp_path: Path) -> None:
    config = _config(tmp_path)

    assert main(_arguments(config, "--execute")) == 1
    assert not (config.output_dir / "storm_plan.json").exists()


def test_plan_only_mode_is_explicit(tmp_path: Path) -> None:
    config = _config(tmp_path)

    assert main(_arguments(config, "--plan-only")) == 0
    assert (config.output_dir / "storm_plan.json").is_file()


def _arguments(config: StormConfig, mode: str) -> list[str]:
    return [
        mode,
        "--system",
        config.system,
        "--link-mbps",
        str(config.link_mbps),
        "--agent-share-pct",
        str(config.agent_share_pct),
        "--burst-intensity",
        config.burst_intensity,
        "--duration-s",
        str(config.duration_s),
        "--seed",
        str(config.seed),
        "--output-dir",
        str(config.output_dir),
        "--task-script",
        str(config.task_script),
    ]
