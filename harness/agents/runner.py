"""Execute seeded MCP task lists; this client is not a substitute framework corpus."""

from __future__ import annotations

import argparse
import http.client
import json
import logging
import math
import os
import random
import signal
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from decimal import Decimal
from ipaddress import IPv4Address
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from common.contracts import TRAINING_LABELS, TrafficClass
from harness.capture import CaptureWindow
from harness.mcp_target.server import PROTOCOL_VERSION

logger = logging.getLogger(__name__)


class RunnerError(RuntimeError):
    """A real runner or tool request failed; its output is retained for inspection."""


@dataclass(frozen=True)
class Task:
    """One explicit MCP tool invocation from a task script."""

    tool: str
    arguments: dict[str, Any]

    def __post_init__(self) -> None:
        if self.tool not in ("search", "fetch", "compute") or not isinstance(self.arguments, dict):
            raise ValueError("tasks require a supported tool and an argument object")
        if len(json.dumps(self.arguments, allow_nan=False)) > 32_768:
            raise ValueError("task arguments exceed the request budget")


@dataclass(frozen=True)
class RunnerConfig:
    """Runner inputs with an explicit seed and scalar or sampled empirical think times."""

    parallelism: int
    think_time: float | tuple[float, ...]
    task_script: tuple[Task, ...]
    seed: int

    def __post_init__(self) -> None:
        if type(self.parallelism) is not int or not 1 <= self.parallelism <= 128:
            raise ValueError("parallelism must be an integer between 1 and 128")
        if type(self.seed) is not int:
            raise ValueError("seed must be an explicit integer")
        if (
            not isinstance(self.task_script, tuple)
            or not self.task_script
            or len(self.task_script) > 10_000
            or any(not isinstance(task, Task) for task in self.task_script)
        ):
            raise ValueError("task_script must contain between 1 and 10000 tasks")
        times = self.think_time if isinstance(self.think_time, tuple) else (self.think_time,)
        if not times or any(
            type(value) not in (float, int) or not math.isfinite(value) or not 0 <= value <= 3600
            for value in times
        ):
            raise ValueError("think_time must contain finite seconds between 0 and 3600")


def plan_tasks(config: RunnerConfig) -> tuple[tuple[Task, float], ...]:
    """Shuffle tasks and draw empirical think times deterministically before concurrency."""
    rng = random.Random(config.seed)
    tasks = list(config.task_script)
    rng.shuffle(tasks)
    times = config.think_time if isinstance(config.think_time, tuple) else (config.think_time,)
    return tuple((task, float(rng.choice(times))) for task in tasks)


class McpClient:
    """Bounded real HTTP client using a fresh, explicitly sourced TCP connection per call."""

    def __init__(self, endpoint: str, source_ip: str) -> None:
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "http"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path != "/mcp"
        ):
            raise ValueError("endpoint must be a plain HTTP /mcp URL without credentials or query")
        self.host = parsed.hostname
        self.port = parsed.port or 80
        self.source_ip = str(IPv4Address(source_ip))

    def request(self, envelope: dict[str, Any]) -> dict[str, Any] | None:
        """Send a JSON-RPC message and reject transport or application failures."""
        connection = http.client.HTTPConnection(
            self.host, self.port, timeout=10, source_address=(self.source_ip, 0)
        )
        try:
            connection.request(
                "POST",
                "/mcp",
                json.dumps(envelope, allow_nan=False),
                {
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                    "MCP-Protocol-Version": PROTOCOL_VERSION,
                },
            )
            response = connection.getresponse()
            body = response.read(65_537)
            if len(body) > 65_536:
                raise RunnerError("MCP response exceeds limit")
            if response.status == 202 and not body:
                return None
            value = json.loads(body)
            if response.status != 200 or not isinstance(value, dict) or "error" in value:
                raise RunnerError(f"MCP request rejected: HTTP {response.status}: {value}")
            if value.get("jsonrpc") != "2.0" or value.get("id") != envelope.get("id"):
                raise RunnerError("MCP response envelope does not match the request")
            if not isinstance(value.get("result"), dict) or value["result"].get("isError"):
                raise RunnerError("MCP tool reported an unsuccessful result")
            return value
        except (OSError, http.client.HTTPException, ValueError) as exc:
            raise RunnerError(f"MCP request failed: {exc}") from exc
        finally:
            connection.close()


def _task_event(client: McpClient, index: int, task: Task, delay: float) -> dict[str, Any]:
    time.sleep(delay)
    started = time.time()
    tick = time.monotonic()
    event: dict[str, Any] = {
        "event": "task",
        "task_index": index,
        "tool": task.tool,
        "start_time_s": started,
    }
    try:
        client.request(
            {
                "jsonrpc": "2.0",
                "id": index + 2,
                "method": "tools/call",
                "params": {"name": task.tool, "arguments": task.arguments},
            }
        )
        event["success"] = True
    except RunnerError as exc:
        logger.error("task %s failed: %s", index, exc)
        event.update(success=False, error=str(exc))
    event.update(end_time_s=time.time(), completion_ms=(time.monotonic() - tick) * 1000)
    return event


def run_tasks(
    config: RunnerConfig, endpoint: str, source_ip: str, label: TrafficClass, output_log: Path
) -> CaptureWindow:
    """Run actual tool calls and log a capture-compatible orchestration interval.

    Labels describe this known task client only; they never claim a Browser Use, Playwright,
    AutoGen or Claude run. Failed tasks retain timing and failure records and raise RunnerError.
    """
    if not isinstance(label, TrafficClass) or label not in TRAINING_LABELS:
        raise ValueError("runner label must be a known training TrafficClass")
    client = McpClient(endpoint, source_ip)
    source = IPv4Address(source_ip)
    with output_log.open("x", encoding="utf-8") as output:
        started = Decimal(str(time.time()))
        output.write(
            json.dumps(
                {
                    "event": "run_start",
                    "source_ip": str(source),
                    "seed": config.seed,
                    "parallelism": config.parallelism,
                    "start_time_s": str(started),
                    "source_framework": "mcp_task_client",
                }
            )
            + "\n"
        )
        output.flush()
        failure: BaseException | None = None
        try:
            client.request(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "mcp_task_client", "version": "1.0"},
                    },
                }
            )
            client.request({"jsonrpc": "2.0", "method": "notifications/initialized"})
            with ThreadPoolExecutor(max_workers=config.parallelism) as executor:
                futures = [
                    executor.submit(_task_event, client, index, task, delay)
                    for index, (task, delay) in enumerate(plan_tasks(config))
                ]
                for future in as_completed(futures):
                    event = future.result()
                    output.write(json.dumps(event, allow_nan=False) + "\n")
                    output.flush()
                    if not event["success"]:
                        failure = RunnerError("one or more tool calls failed; inspect runner log")
        except BaseException as exc:
            failure = exc
        finally:
            window = CaptureWindow(
                source, started, Decimal(str(time.time())), label, "mcp_task_client"
            )
            # §7.20: ground truth comes from runner-owned source/time, never packet features.
            output.write(
                json.dumps(
                    {
                        "event": "run_end",
                        "success": failure is None,
                        "error": str(failure) if failure else None,
                        "capture_window": {
                            "source_ip": str(window.source_ip),
                            "start_time_s": str(window.start_time_s),
                            "end_time_s": str(window.end_time_s),
                            "label": int(window.label),
                            "source_framework": window.source_framework,
                        },
                    }
                )
                + "\n"
            )
        if failure is not None:
            raise failure
    return window


def _signal_framework_group(process_group: int, signum: int) -> bool:
    try:
        os.killpg(process_group, signum)
    except ProcessLookupError:
        logger.debug("framework process group %s already exited", process_group, exc_info=True)
        return False
    return True


def _stop_framework_group(child: subprocess.Popen[bytes]) -> None:
    # §7.19: descendants must stop before another experiment can use the host.
    if _signal_framework_group(child.pid, signal.SIGTERM):
        deadline = time.monotonic() + 0.3
        while time.monotonic() < deadline:
            child.poll()  # Reap the leader even when descendants outlive it.
            if not _signal_framework_group(child.pid, 0):
                break
            time.sleep(0.01)
        else:
            _signal_framework_group(child.pid, signal.SIGKILL)
    child.wait(timeout=3)


def run_external_framework(command: tuple[str, ...], output_log: Path, timeout_s: float) -> None:
    """Execute an explicitly supplied genuine framework command, preserving its output.

    The framework must implement its own seed/task inputs and emit capture windows from its
    actual container address. This boundary deliberately emits no labels or framework claims.
    The command and descendants share an owned process group, stopped on every exit path.
    """
    if not command or any(not isinstance(part, str) or not part for part in command):
        raise ValueError("command must contain nonempty argv strings")
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError("timeout_s must be positive and finite")
    with output_log.open("xb") as output:
        try:
            child = subprocess.Popen(
                command, stdout=output, stderr=subprocess.STDOUT, start_new_session=True
            )
            try:
                returncode = child.wait(timeout=timeout_s)
            finally:
                _stop_framework_group(child)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RunnerError(f"external framework failed; inspect {output_log}") from exc
    if returncode:
        raise RunnerError(f"external framework exited {returncode}; inspect {output_log}")


def main() -> None:
    """Run a JSON task script against a real MCP target with explicit experiment inputs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--source-ip", required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--parallelism", type=int, required=True)
    parser.add_argument("--think-time", type=float, required=True)
    parser.add_argument("--task-script", type=Path, required=True)
    parser.add_argument(
        "--label", type=int, choices=[int(label) for label in TRAINING_LABELS], required=True
    )
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args()
    payload = json.loads(args.task_script.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or any(
        not isinstance(task, dict) or set(task) != {"tool", "arguments"} for task in payload
    ):
        raise ValueError("task script must be a list of objects with tool and arguments")
    config = RunnerConfig(
        args.parallelism, args.think_time, tuple(Task(**task) for task in payload), args.seed
    )
    logging.basicConfig(level=logging.INFO)
    window = run_tasks(config, args.endpoint, args.source_ip, TrafficClass(args.label), args.log)
    logger.info("runner completed: %s", window)


if __name__ == "__main__":
    main()
