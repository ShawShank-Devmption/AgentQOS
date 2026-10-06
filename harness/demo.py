"""Deterministic command plan for the centerpiece human-plus-agent storm."""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from common.contracts import (
    BURST_INTENSITIES,
    EXPERIMENT_SYSTEMS,
    MAX_AGENT_SHARE_PCT,
    MAX_LINK_MBPS,
    MIN_AGENT_SHARE_PCT,
    MIN_LINK_MBPS,
)

LOGGER = logging.getLogger(__name__)
AGENT_MODULES = (
    "harness.agents.browser_use",
    "harness.agents.playwright_agent",
    "harness.agents.autogen",
    "harness.agents.claude_mcp",
)
BURST_PARALLELISM = {"low": 2, "med": 10, "high": 50}
BURST_FLOWS_PER_SECOND = {"low": 5, "med": 20, "high": 50}
AGENT_SOURCE_IPS = ("10.0.0.1", "10.0.0.4", "10.0.0.5", "10.0.0.6")


@dataclass(frozen=True)
class StormConfig:
    """Validated, seeded demo scenario inputs."""

    system: str
    link_mbps: int
    agent_share_pct: int
    burst_intensity: str
    duration_s: int
    seed: int
    output_dir: Path
    task_script: Path

    def __post_init__(self) -> None:
        if self.system not in EXPERIMENT_SYSTEMS:
            raise ValueError(f"system must be one of {EXPERIMENT_SYSTEMS}")
        if not MIN_LINK_MBPS <= self.link_mbps <= MAX_LINK_MBPS:
            raise ValueError(f"link_mbps must be between {MIN_LINK_MBPS} and {MAX_LINK_MBPS}")
        if not MIN_AGENT_SHARE_PCT <= self.agent_share_pct <= MAX_AGENT_SHARE_PCT:
            raise ValueError(
                f"agent_share_pct must be between {MIN_AGENT_SHARE_PCT} and {MAX_AGENT_SHARE_PCT}"
            )
        if self.burst_intensity not in BURST_INTENSITIES:
            raise ValueError(f"burst_intensity must be in {BURST_INTENSITIES}")
        if self.duration_s <= 0:
            raise ValueError("duration_s must be positive")
        if self.seed < 0:
            raise ValueError("seed must be non-negative")
        if not self.task_script.is_file():
            raise FileNotFoundError(self.task_script)


@dataclass(frozen=True)
class StormPhase:
    """Commands that belong to one ordered demo phase."""

    name: str
    commands: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class StormPlan:
    """Serializable demo plan that separates system-on from baseline runs."""

    system: str
    protection_mode: str
    link_mbps: int
    agent_share_pct: int
    burst_intensity: str
    duration_s: int
    seed: int
    phases: tuple[StormPhase, ...]


def build_storm_plan(config: StormConfig) -> StormPlan:
    """Build the service, human warm-up, and agent-storm command phases.

    Args:
        config: Validated scenario inputs.

    Returns:
        An immutable, deterministically ordered command plan.
    """
    target_url = "http://10.0.0.100:8080/mcp"
    orchestration_log = config.output_dir / "orchestration.jsonl"
    services = StormPhase(
        "services",
        (
            (
                sys.executable,
                "-m",
                "harness.mcp_target.server",
                "--host",
                "10.0.0.100",
                "--port",
                "8080",
                "--log-path",
                str(config.output_dir / "mcp_requests.jsonl"),
            ),
            (
                sys.executable,
                "-m",
                "dashboard.server",
                "--snapshot",
                str(config.output_dir / "live_metrics.json"),
            ),
        ),
    )
    human = StormPhase(
        "human-warmup",
        (
            (
                "iperf3",
                "-c",
                "10.0.0.100",
                "-t",
                str(config.duration_s),
                "-J",
            ),
        ),
    )
    task_count = _task_count(config.task_script)
    share_fraction = config.agent_share_pct / 100
    parallelism = max(
        1,
        math.ceil(BURST_PARALLELISM[config.burst_intensity] * share_fraction),
    )
    total_calls = math.ceil(
        BURST_FLOWS_PER_SECOND[config.burst_intensity] * share_fraction * config.duration_s
    )
    repetitions = max(1, math.ceil(total_calls / (len(AGENT_MODULES) * task_count)))
    agents = StormPhase(
        "agent-storm",
        tuple(
            (
                sys.executable,
                "-m",
                module,
                "--target-url",
                target_url,
                "--task-script",
                str(config.task_script),
                "--source-ip",
                AGENT_SOURCE_IPS[index],
                "--label",
                "agent-interactive" if index < 2 else "agent-bulk",
                "--parallelism",
                str(parallelism),
                "--think-time",
                "0.0",
                "--seed",
                str(config.seed),
                "--repetitions",
                str(repetitions),
                "--orchestration-log",
                str(orchestration_log),
            )
            for index, module in enumerate(AGENT_MODULES)
        ),
    )
    return StormPlan(
        system=config.system,
        protection_mode="system-on" if config.system == "ours" else "system-off",
        link_mbps=config.link_mbps,
        agent_share_pct=config.agent_share_pct,
        burst_intensity=config.burst_intensity,
        duration_s=config.duration_s,
        seed=config.seed,
        phases=(services, human, agents),
    )


def _task_count(task_script: Path) -> int:
    try:
        tasks = json.loads(task_script.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not parse task script: {task_script}") from exc
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("task script must be a non-empty JSON list")
    return len(tasks)


def write_storm_plan(plan: StormPlan, output_path: Path) -> None:
    """Write a deterministic JSON representation of a storm plan.

    Args:
        plan: Plan returned by `build_storm_plan`.
        output_path: JSON destination.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(asdict(plan), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def require_live_executor() -> None:
    """Fail until the persistent Person 1/2 data-plane orchestrator exists.

    Raises:
        RuntimeError: Always, while the required P4/controller pipeline is absent.
    """
    raise RuntimeError(
        "persistent experiment execution is unavailable: the Person 1/2 "
        "agent-aware P4 data plane and controller pipeline are not present"
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Validate and materialize one storm plan for Linux orchestration.

    Args:
        argv: Optional command-line arguments.

    Returns:
        Zero when a valid plan is written.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan-only", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("--system", required=True)
    parser.add_argument("--link-mbps", type=int, required=True)
    parser.add_argument("--agent-share-pct", type=int, required=True)
    parser.add_argument("--burst-intensity", required=True)
    parser.add_argument("--duration-s", type=int, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--task-script",
        type=Path,
        default=Path("harness/tasks/storm.json"),
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        config = StormConfig(
            system=args.system,
            link_mbps=args.link_mbps,
            agent_share_pct=args.agent_share_pct,
            burst_intensity=args.burst_intensity,
            duration_s=args.duration_s,
            seed=args.seed,
            output_dir=args.output_dir,
            task_script=args.task_script,
        )
        if args.execute:
            require_live_executor()
        output_path = args.output_dir / "storm_plan.json"
        write_storm_plan(build_storm_plan(config), output_path)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        LOGGER.error("storm plan is invalid: %s", exc)
        return 1
    LOGGER.info("wrote storm plan: %s", output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
