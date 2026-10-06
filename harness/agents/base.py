"""Deterministic runner contract shared by real agent-framework adapters."""

from __future__ import annotations

import json
import logging
import random
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from decimal import Decimal
from ipaddress import IPv4Address
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from common.contracts import TRAINING_LABELS, TrafficClass
from harness.capture import CaptureWindow

LOGGER = logging.getLogger(__name__)
ToolCall = Callable[[str, str, dict[str, object]], object]
Sleeper = Callable[[float], None]


@dataclass(frozen=True)
class AgentTask:
    """One MCP tool call loaded from a deterministic task script."""

    tool: str
    arguments: dict[str, object]


@dataclass(frozen=True)
class RunnerConfig:
    """Shared runner inputs required by design section 9."""

    target_url: str
    task_script: Path
    source_ip: IPv4Address
    label: TrafficClass
    parallelism: int
    think_time_s: float
    seed: int

    def __post_init__(self) -> None:
        parsed_url = urlparse(self.target_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError("target_url must be an absolute HTTP(S) URL")
        if not self.task_script.is_file():
            raise FileNotFoundError(self.task_script)
        if not isinstance(self.source_ip, IPv4Address):
            raise TypeError("source_ip must be an IPv4Address")
        if self.label not in TRAINING_LABELS:
            raise ValueError("runner label cannot be UNKNOWN")
        if self.parallelism <= 0:
            raise ValueError("parallelism must be positive")
        if self.think_time_s < 0:
            raise ValueError("think_time_s must be non-negative")
        if self.seed < 0:
            raise ValueError("seed must be non-negative")


@dataclass(frozen=True)
class RunnerRecord:
    """Runner result and orchestration-derived capture window."""

    window: CaptureWindow
    completed: int
    failed: int


def load_tasks(config: RunnerConfig) -> tuple[AgentTask, ...]:
    """Load, validate, and deterministically shuffle one task script.

    Args:
        config: Runner configuration containing the task script and seed.

    Returns:
        Validated tasks in seeded execution order.

    Raises:
        ValueError: If the task script is not a non-empty valid task list.
    """
    try:
        raw = json.loads(config.task_script.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not parse task script: {config.task_script}") from exc
    if not isinstance(raw, list) or not raw:
        raise ValueError("task script must be a non-empty JSON list")

    tasks: list[AgentTask] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise ValueError(f"task {index} must be a JSON object")
        tool = item.get("tool")
        arguments = item.get("arguments")
        if not isinstance(tool, str) or not tool.strip() or not isinstance(arguments, Mapping):
            raise ValueError(f"task {index} requires tool and arguments")
        if not all(isinstance(key, str) for key in arguments):
            raise ValueError(f"task {index} argument keys must be strings")
        tasks.append(AgentTask(tool.strip(), dict(arguments)))

    random.Random(config.seed).shuffle(tasks)
    return tuple(tasks)


def run_agent(
    config: RunnerConfig,
    source_framework: str,
    *,
    tool_call: ToolCall | None = None,
    sleeper: Sleeper = time.sleep,
) -> RunnerRecord:
    """Execute a seeded task list and emit its orchestration capture window.

    Args:
        config: Validated target, load, label, and seed settings.
        source_framework: Stable framework identifier for `labels.csv`.
        tool_call: Optional outbound MCP boundary used by tests and adapters.
        sleeper: Sleep boundary used to apply configured think time.

    Returns:
        Completion counts and a ground-truth capture window.
    """
    if not source_framework.strip():
        raise ValueError("source_framework must not be empty")
    tasks = load_tasks(config)
    call = tool_call or send_tool_call
    start_time = Decimal(str(time.time()))
    completed = 0
    failed = 0
    with ThreadPoolExecutor(max_workers=config.parallelism) as executor:
        futures = [executor.submit(_execute_task, config, task, call, sleeper) for task in tasks]
        for future in as_completed(futures):
            try:
                future.result()
            except (OSError, RuntimeError, ValueError, urllib.error.URLError) as exc:
                failed += 1
                LOGGER.warning("agent task failed: %s", exc)
            else:
                completed += 1
    end_time = Decimal(str(time.time()))
    window = CaptureWindow(
        source_ip=config.source_ip,
        start_time_s=start_time,
        end_time_s=end_time,
        label=config.label,
        source_framework=source_framework.strip(),
    )
    return RunnerRecord(window=window, completed=completed, failed=failed)


def send_tool_call(target_url: str, tool: str, arguments: dict[str, object]) -> object:
    """Send one MCP `tools/call` request to the instrumented target.

    Args:
        target_url: Absolute MCP HTTP endpoint.
        tool: Tool name.
        arguments: JSON-compatible tool arguments.

    Returns:
        The JSON-RPC result object.

    Raises:
        RuntimeError: If the target returns invalid JSON or a JSON-RPC error.
    """
    payload = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        },
        sort_keys=True,
    ).encode("utf-8")
    request = urllib.request.Request(
        target_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            decoded: Any = json.loads(response.read())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError("MCP target returned invalid JSON") from exc
    if not isinstance(decoded, Mapping):
        raise RuntimeError("MCP target returned a non-object response")
    if "error" in decoded:
        raise RuntimeError(f"MCP target rejected tool call: {decoded['error']}")
    if "result" not in decoded:
        raise RuntimeError("MCP target response omitted result")
    return decoded["result"]


def _execute_task(
    config: RunnerConfig,
    task: AgentTask,
    tool_call: ToolCall,
    sleeper: Sleeper,
) -> object:
    if config.think_time_s:
        sleeper(config.think_time_s)
    return tool_call(config.target_url, task.tool, task.arguments)
