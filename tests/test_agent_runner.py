"""Tests for the deterministic agent runner contract."""

import json
import threading
from ipaddress import IPv4Address
from pathlib import Path

import pytest

from common.contracts import TrafficClass
from harness.agents.base import RunnerConfig, load_tasks, run_agent


def _write_tasks(path: Path, count: int = 5) -> None:
    tasks = [
        {"tool": "compute", "arguments": {"expression": f"{index}+1"}} for index in range(count)
    ]
    path.write_text(json.dumps(tasks), encoding="utf-8")


def _config(task_script: Path, **overrides: object) -> RunnerConfig:
    values: dict[str, object] = {
        "target_url": "http://127.0.0.1:8080/mcp",
        "task_script": task_script,
        "source_ip": IPv4Address("10.0.0.1"),
        "label": TrafficClass.AGENT_INTERACTIVE,
        "parallelism": 2,
        "think_time_s": 0.0,
        "seed": 7,
    }
    values.update(overrides)
    return RunnerConfig(**values)  # type: ignore[arg-type]


def test_task_order_is_deterministic_for_explicit_seed(tmp_path: Path) -> None:
    task_script = tmp_path / "tasks.json"
    _write_tasks(task_script)

    first = load_tasks(_config(task_script, seed=23))
    second = load_tasks(_config(task_script, seed=23))
    different = load_tasks(_config(task_script, seed=24))

    assert first == second
    assert first != different


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("parallelism", 0, "parallelism must be positive"),
        ("think_time_s", -0.1, "think_time_s must be non-negative"),
        ("repetitions", 0, "repetitions must be positive"),
        ("label", TrafficClass.UNKNOWN, "UNKNOWN"),
    ],
)
def test_runner_config_rejects_invalid_inputs(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    task_script = tmp_path / "tasks.json"
    _write_tasks(task_script)
    with pytest.raises(ValueError, match=message):
        _config(task_script, **{field: value})


def test_parallel_runner_overlaps_calls_and_emits_capture_window(tmp_path: Path) -> None:
    task_script = tmp_path / "tasks.json"
    _write_tasks(task_script, count=2)
    barrier = threading.Barrier(2, timeout=1)

    def overlapping_call(target_url: str, tool: str, arguments: dict[str, object]) -> object:
        assert target_url.endswith("/mcp")
        assert tool == "compute"
        barrier.wait()
        return arguments

    record = run_agent(
        _config(task_script),
        source_framework="playwright-agent",
        tool_call=overlapping_call,
    )

    assert record.completed == 2
    assert record.failed == 0
    assert record.window.source_ip == IPv4Address("10.0.0.1")
    assert record.window.label == TrafficClass.AGENT_INTERACTIVE
    assert record.window.source_framework == "playwright-agent"
    assert record.window.end_time_s >= record.window.start_time_s


def test_runner_applies_configured_think_time_to_each_task(tmp_path: Path) -> None:
    task_script = tmp_path / "tasks.json"
    _write_tasks(task_script, count=3)
    sleeps: list[float] = []

    run_agent(
        _config(task_script, parallelism=1, think_time_s=0.25),
        source_framework="fixture",
        tool_call=lambda target_url, tool, arguments: arguments,
        sleeper=sleeps.append,
    )

    assert sleeps == [0.25, 0.25, 0.25]


def test_runner_repeats_script_to_sustain_an_experiment_window(tmp_path: Path) -> None:
    task_script = tmp_path / "tasks.json"
    _write_tasks(task_script, count=2)

    record = run_agent(
        _config(task_script, repetitions=3),
        source_framework="fixture",
        tool_call=lambda target_url, tool, arguments: arguments,
    )

    assert record.completed == 6
    assert record.failed == 0
