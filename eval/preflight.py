"""Report Linux runtime and artifact blockers before an experiment creates outputs."""

from __future__ import annotations

import argparse
import importlib.util
import logging
import os
import platform
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from eval.run_experiment import ConfigError, ExperimentConfig, expand_runs, load_config

LOGGER = logging.getLogger(__name__)
COMMON_COMMANDS = frozenset({"simple_switch", "simple_switch_CLI", "tshark", "iperf3", "tc"})
OURS_INTEGRATION_PATHS = (
    Path("p4src/agent_aware.p4"),
    Path("build/agent_aware.json"),
    Path("controller/policy.py"),
    Path("controller/punt_handler.py"),
    Path("controller/reclassifier.py"),
)


@dataclass(frozen=True)
class RuntimeFacts:
    """Read-only facts collected from the experiment host."""

    platform: str
    effective_uid: int
    available_commands: frozenset[str]
    mininet_available: bool


@dataclass(frozen=True)
class PreflightCheck:
    """One named readiness assertion and its operator-facing detail."""

    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class PreflightReport:
    """Complete readiness report for one validated experiment config."""

    checks: tuple[PreflightCheck, ...]

    @property
    def ready(self) -> bool:
        """Return whether every readiness assertion passed."""
        return all(check.passed for check in self.checks)

    @property
    def failures(self) -> tuple[PreflightCheck, ...]:
        """Return failed assertions in deterministic check order."""
        return tuple(check for check in self.checks if not check.passed)


def collect_runtime_facts() -> RuntimeFacts:
    """Collect the host facts used by experiment preflight."""
    command_names = COMMON_COMMANDS | {"nginx"}
    return RuntimeFacts(
        platform=platform.system().lower(),
        effective_uid=os.geteuid(),
        available_commands=frozenset(
            command for command in command_names if shutil.which(command) is not None
        ),
        mininet_available=importlib.util.find_spec("mininet") is not None,
    )


def check_preconditions(
    config: ExperimentConfig,
    project_root: Path,
    facts: RuntimeFacts,
) -> PreflightReport:
    """Check runtime, artifacts, and append-only paths without mutating them.

    Args:
        config: Validated frozen-schema experiment configuration.
        project_root: Repository root used to resolve artifacts and outputs.
        facts: Host observations, injectable for deterministic tests.

    Returns:
        An ordered report containing every applicable readiness check.
    """
    required_commands = set(COMMON_COMMANDS)
    if config.system == "app_limiter":
        required_commands.add("nginx")
    missing_commands = sorted(required_commands - facts.available_commands)
    p4_json_name = "agent_aware.json" if config.system == "ours" else "l2fwd.json"
    p4_json_path = project_root / "build" / p4_json_name
    task_script_path = project_root / "harness" / "tasks" / "storm.json"
    existing_cells = tuple(
        run.output_dir.relative_to(project_root).as_posix()
        for run in expand_runs(config, project_root)
        if run.output_dir.exists()
    )
    checks = [
        PreflightCheck("linux", facts.platform == "linux", f"detected {facts.platform}"),
        PreflightCheck(
            "root",
            facts.effective_uid == 0,
            f"effective uid is {facts.effective_uid}; Mininet execution requires root",
        ),
        PreflightCheck(
            "commands",
            not missing_commands,
            "all runtime commands found"
            if not missing_commands
            else f"missing: {', '.join(missing_commands)}",
        ),
        PreflightCheck(
            "mininet",
            facts.mininet_available,
            "Python can import mininet"
            if facts.mininet_available
            else "Python cannot import mininet",
        ),
        PreflightCheck(
            "p4-json",
            p4_json_path.is_file(),
            p4_json_path.relative_to(project_root).as_posix(),
        ),
        PreflightCheck(
            "task-script",
            task_script_path.is_file(),
            task_script_path.relative_to(project_root).as_posix(),
        ),
        PreflightCheck(
            "append-only-output",
            not existing_cells,
            "all cell paths are new"
            if not existing_cells
            else f"existing cell: {existing_cells[0]}",
        ),
    ]
    if config.system == "ours":
        missing_paths = tuple(
            path.as_posix()
            for path in OURS_INTEGRATION_PATHS
            if not (project_root / path).is_file()
        )
        checks.append(
            PreflightCheck(
                "ours-integration",
                not missing_paths,
                "Dev A/Dev B artifacts present"
                if not missing_paths
                else f"missing: {', '.join(missing_paths)}",
            )
        )
    return PreflightReport(tuple(checks))


def main(argv: Sequence[str] | None = None) -> int:
    """Run preflight for one config and return nonzero for any blocker."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        config = load_config(args.config)
        report = check_preconditions(config, args.project_root.resolve(), collect_runtime_facts())
    except (ConfigError, FileNotFoundError, OSError, ValueError) as exc:
        LOGGER.error("preflight could not run: %s", exc)
        return 1
    for check in report.checks:
        log = LOGGER.info if check.passed else LOGGER.error
        log("%s %s: %s", "PASS" if check.passed else "FAIL", check.name, check.detail)
    return 0 if report.ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
