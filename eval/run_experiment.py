"""Validate, expand, lock, and execute reproducible experiment configurations."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import logging
import math
import os
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import product
from pathlib import Path
from typing import cast

import yaml

from common.contracts import (
    BURST_INTENSITIES,
    EXPERIMENT_CONFIG_FIELDS,
    EXPERIMENT_SYSTEMS,
    EXPERIMENT_TOPOLOGY,
    MAX_AGENT_SHARE_PCT,
    MAX_LINK_MBPS,
    MIN_AGENT_SHARE_PCT,
    MIN_LINK_MBPS,
    TRAINING_LABELS,
)
from eval.baselines.base import baseline_plan

LOGGER = logging.getLogger(__name__)
HOST_LOCK_PATH = Path("/tmp/agentqos-experiment.lock")
RUN_CLASS_METRICS = ("throughput_mbps", "p50_ms", "p95_ms", "p99_ms")


class ConfigError(ValueError):
    """Raised when an experiment configuration violates design section 4.6."""


class ExperimentLockedError(RuntimeError):
    """Raised when another experiment already owns the host lock."""


@dataclass(frozen=True)
class ExperimentConfig:
    """Validated, immutable experiment configuration."""

    name: str
    system: str
    topology: str
    link_mbps: int
    agent_share_pct: tuple[int, ...]
    burst_intensity: tuple[str, ...]
    duration_s: int
    seeds: tuple[int, ...]
    outputs: Path


@dataclass(frozen=True)
class RunSpec:
    """One immutable cell in the experiment grid."""

    name: str
    system: str
    topology: str
    link_mbps: int
    agent_share_pct: int
    burst_intensity: str
    duration_s: int
    seed: int
    config_hash: str
    output_dir: Path


@dataclass(frozen=True)
class RunArtifactDigests:
    """SHA-256 identities required before a run can become complete."""

    run_summary_sha256: str
    packet_telemetry_sha256: str
    ingress_packet_telemetry_sha256: str
    controller_lifecycle_sha256: str | None


CommandRunner = Callable[[tuple[str, ...]], None]


class ExperimentLock(AbstractContextManager["ExperimentLock"]):
    """Non-blocking, host-wide lock for experiment result integrity (§7.19)."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._handle: object | None = None

    def __enter__(self) -> ExperimentLock:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = self._path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise ExperimentLockedError(
                f"another experiment is already running; lock: {self._path}"
            ) from exc
        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()}\n")
        handle.flush()
        self._handle = handle
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        del exc_type, exc_value, traceback
        handle = self._handle
        if handle is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]
            handle.close()  # type: ignore[attr-defined]
            self._handle = None


def load_config(config_path: Path) -> ExperimentConfig:
    """Load and validate the frozen experiment YAML schema.

    Args:
        config_path: YAML file following design section 4.6.

    Returns:
        An immutable validated configuration.

    Raises:
        FileNotFoundError: If the YAML file does not exist.
        ConfigError: If YAML parsing or schema validation fails.
    """
    if not config_path.is_file():
        raise FileNotFoundError(config_path)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ConfigError(f"could not parse experiment config: {config_path}") from exc

    values = _require_mapping(raw)
    expected = set(EXPERIMENT_CONFIG_FIELDS)
    missing = expected - values.keys()
    extra = values.keys() - expected
    if missing:
        raise ConfigError(f"missing experiment fields: {sorted(missing)}")
    if extra:
        raise ConfigError(f"unknown experiment fields: {sorted(extra)}")

    name = _require_string(values["name"], "name")
    system = _require_string(values["system"], "system")
    topology = _require_string(values["topology"], "topology")
    link_mbps = _require_int(values["link_mbps"], "link_mbps")
    duration_s = _require_int(values["duration_s"], "duration_s")
    shares = _require_int_tuple(values["agent_share_pct"], "agent_share_pct")
    bursts = _require_string_tuple(values["burst_intensity"], "burst_intensity")
    seeds = _require_int_tuple(values["seeds"], "seeds")
    output_text = _require_string(values["outputs"], "outputs")

    if system not in EXPERIMENT_SYSTEMS:
        raise ConfigError(f"system must be one of {EXPERIMENT_SYSTEMS}")
    if topology != EXPERIMENT_TOPOLOGY:
        raise ConfigError(f"topology must be {EXPERIMENT_TOPOLOGY}")
    if link_mbps < MIN_LINK_MBPS or link_mbps > MAX_LINK_MBPS:
        raise ConfigError(f"link_mbps must be between {MIN_LINK_MBPS} and {MAX_LINK_MBPS}")
    if duration_s <= 0:
        raise ConfigError("duration_s must be positive")
    if any(share < MIN_AGENT_SHARE_PCT or share > MAX_AGENT_SHARE_PCT for share in shares):
        raise ConfigError(
            f"agent_share_pct values must be between {MIN_AGENT_SHARE_PCT} "
            f"and {MAX_AGENT_SHARE_PCT}"
        )
    if len(set(shares)) != len(shares):
        raise ConfigError("agent_share_pct values must be unique")
    if any(burst not in BURST_INTENSITIES for burst in bursts):
        raise ConfigError(f"burst_intensity values must be in {BURST_INTENSITIES}")
    if len(set(bursts)) != len(bursts):
        raise ConfigError("burst_intensity values must be unique")
    if any(seed < 0 for seed in seeds):
        raise ConfigError("seeds must be non-negative")
    if len(set(seeds)) != len(seeds):
        raise ConfigError("seeds must be unique")

    outputs = Path(output_text)
    if (
        outputs.is_absolute()
        or len(outputs.parts) < 2
        or outputs.parts[0] != "results"
        or ".." in outputs.parts
    ):
        raise ConfigError("outputs must be a relative path below results/")

    return ExperimentConfig(
        name=name,
        system=system,
        topology=topology,
        link_mbps=link_mbps,
        agent_share_pct=shares,
        burst_intensity=bursts,
        duration_s=duration_s,
        seeds=seeds,
        outputs=outputs,
    )


def config_hash(config: ExperimentConfig) -> str:
    """Return the stable SHA-256 identity of a validated experiment config.

    Args:
        config: Validated frozen-schema configuration.

    Returns:
        Lowercase hexadecimal SHA-256 digest.
    """
    payload = {
        "name": config.name,
        "system": config.system,
        "topology": config.topology,
        "link_mbps": config.link_mbps,
        "agent_share_pct": list(config.agent_share_pct),
        "burst_intensity": list(config.burst_intensity),
        "duration_s": config.duration_s,
        "seeds": list(config.seeds),
        "outputs": config.outputs.as_posix().rstrip("/"),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def expand_runs(config: ExperimentConfig, project_root: Path) -> tuple[RunSpec, ...]:
    """Expand one config into deterministic share × burst × seed cells.

    Args:
        config: Validated grid definition.
        project_root: Repository root beneath which result paths resolve.

    Returns:
        Run specifications in share, burst, then seed order.
    """
    digest = config_hash(config)
    output_root = project_root / config.outputs / digest
    return tuple(
        RunSpec(
            name=config.name,
            system=config.system,
            topology=config.topology,
            link_mbps=config.link_mbps,
            agent_share_pct=share,
            burst_intensity=burst,
            duration_s=config.duration_s,
            seed=seed,
            config_hash=digest,
            output_dir=(output_root / f"{config.system}-share-{share}-burst-{burst}-seed-{seed}"),
        )
        for share, burst, seed in product(
            config.agent_share_pct,
            config.burst_intensity,
            config.seeds,
        )
    )


def execute_run(
    run: RunSpec,
    *,
    dry_run: bool = False,
    command_runner: CommandRunner | None = None,
) -> None:
    """Execute one append-only experiment cell and persist its manifest.

    Args:
        run: Immutable cell returned by `expand_runs`.
        dry_run: Create a planned manifest without invoking external commands.
        command_runner: Optional external command boundary for tests.

    Raises:
        FileExistsError: If this config-hash/seed cell already exists.
        RuntimeError: If setup, workload, or teardown fails.
    """
    run.output_dir.mkdir(parents=True, exist_ok=False)
    plan = baseline_plan(run.system, run.link_mbps, run.output_dir)
    workload = _workload_command(run)
    manifest: dict[str, object] = {
        **_run_values(run),
        "status": "planned" if dry_run else "running",
        "started_at": datetime.now(UTC).isoformat(),
        "setup_commands": [list(command) for command in plan.setup_commands],
        "workload_command": list(workload),
        "teardown_commands": [list(command) for command in plan.teardown_commands],
    }
    manifest_path = run.output_dir / "manifest.json"
    _write_manifest(manifest_path, manifest)
    if dry_run:
        return

    runner = command_runner or _run_command
    try:
        runner(workload)
        artifact_digests = _validate_run_summary(run)
    except Exception as exc:
        # §7.19: preserve the failed append-only cell and diagnostic; never reuse it silently.
        manifest["status"] = "failed"
        manifest["error"] = str(exc)
        manifest["finished_at"] = datetime.now(UTC).isoformat()
        _write_manifest(manifest_path, manifest)
        raise
    manifest["status"] = "complete"
    manifest.update(
        {
            "run_summary_sha256": artifact_digests.run_summary_sha256,
            "packet_telemetry_sha256": artifact_digests.packet_telemetry_sha256,
            "ingress_packet_telemetry_sha256": (artifact_digests.ingress_packet_telemetry_sha256),
        }
    )
    if artifact_digests.controller_lifecycle_sha256 is not None:
        manifest["controller_lifecycle_sha256"] = artifact_digests.controller_lifecycle_sha256
    manifest["finished_at"] = datetime.now(UTC).isoformat()
    _write_manifest(manifest_path, manifest)


def execute_config(
    config: ExperimentConfig,
    project_root: Path,
    lock_path: Path,
    *,
    dry_run: bool = False,
    command_runner: CommandRunner | None = None,
) -> tuple[RunSpec, ...]:
    """Execute all cells while holding the exclusive host lock.

    Args:
        config: Validated experiment grid.
        project_root: Repository root for output resolution.
        lock_path: Host-global lock file path.
        dry_run: Write planned manifests without running commands.
        command_runner: Optional external command boundary for tests.

    Returns:
        The complete ordered run matrix.
    """
    runs = expand_runs(config, project_root)
    with ExperimentLock(lock_path):
        output_dirs = tuple(run.output_dir for run in runs)
        if len(set(output_dirs)) != len(output_dirs):
            raise ConfigError("experiment matrix resolves to duplicate output directories")
        existing = next((path for path in output_dirs if path.exists()), None)
        if existing is not None:
            raise FileExistsError(existing)
        for run in runs:
            execute_run(run, dry_run=dry_run, command_runner=command_runner)
    return runs


def main(argv: Sequence[str] | None = None) -> int:
    """Validate or execute one experiment configuration from the command line.

    Args:
        argv: Optional command-line arguments.

    Returns:
        Zero for a valid config, otherwise one.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        config = load_config(args.config)
        if args.dry_run and not args.execute:
            raise ConfigError("--dry-run requires --execute")
        if args.execute:
            runs = execute_config(
                config,
                args.project_root,
                HOST_LOCK_PATH,
                dry_run=args.dry_run,
            )
            LOGGER.info("processed %d experiment cells", len(runs))
    except (
        ConfigError,
        ExperimentLockedError,
        FileExistsError,
        FileNotFoundError,
        RuntimeError,
    ) as exc:
        LOGGER.error("experiment config is invalid: %s", exc)
        return 1
    LOGGER.info(
        "validated experiment %s: system=%s seeds=%d",
        config.name,
        config.system,
        len(config.seeds),
    )
    return 0


def _run_values(run: RunSpec) -> dict[str, object]:
    return {
        "name": run.name,
        "system": run.system,
        "topology": run.topology,
        "link_mbps": run.link_mbps,
        "agent_share_pct": run.agent_share_pct,
        "burst_intensity": run.burst_intensity,
        "duration_s": run.duration_s,
        "seed": run.seed,
        "config_hash": run.config_hash,
        "output_dir": str(run.output_dir),
    }


def _workload_command(run: RunSpec) -> tuple[str, ...]:
    p4_json = "build/agent_aware.json" if run.system == "ours" else "build/l2fwd.json"
    return (
        sys.executable,
        "-m",
        "harness.demo",
        "--execute",
        "--system",
        run.system,
        "--link-mbps",
        str(run.link_mbps),
        "--agent-share-pct",
        str(run.agent_share_pct),
        "--burst-intensity",
        run.burst_intensity,
        "--duration-s",
        str(run.duration_s),
        "--seed",
        str(run.seed),
        "--output-dir",
        str(run.output_dir),
        "--p4-json",
        p4_json,
    )


def _validate_run_summary(run: RunSpec) -> RunArtifactDigests:
    summary_path = run.output_dir / "run_summary.json"
    if not summary_path.is_file():
        raise RuntimeError(f"workload did not produce run_summary.json: {summary_path}")
    try:
        encoded = summary_path.read_bytes()
        raw = json.loads(encoded)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"could not parse run_summary.json: {summary_path}") from exc
    if not isinstance(raw, Mapping):
        raise RuntimeError("run_summary.json must contain a JSON object")
    expected_coordinates = {
        "system": run.system,
        "link_mbps": run.link_mbps,
        "agent_share_pct": run.agent_share_pct,
        "burst_intensity": run.burst_intensity,
        "duration_s": run.duration_s,
        "seed": run.seed,
    }
    for field, expected in expected_coordinates.items():
        if raw.get(field) != expected:
            raise RuntimeError(f"run_summary.json {field} does not match the run manifest")
    classes = raw.get("classes")
    required_classes = {traffic_class.name for traffic_class in TRAINING_LABELS}
    if not isinstance(classes, Mapping) or set(classes) != required_classes:
        raise RuntimeError("run_summary.json has invalid class coverage")
    for class_name in required_classes:
        metrics = classes[class_name]
        if not isinstance(metrics, Mapping) or set(metrics) != set(RUN_CLASS_METRICS):
            raise RuntimeError(f"run_summary.json has invalid {class_name} metrics")
        for metric in RUN_CLASS_METRICS:
            _nonnegative_number(metrics.get(metric), f"{class_name}.{metric}")
    if raw.get("latency_method") != "matched_two_tap":
        raise RuntimeError("run_summary.json requires matched two-tap latency")
    latency_match = raw.get("latency_match")
    if not isinstance(latency_match, Mapping):
        raise RuntimeError("run_summary.json requires two-tap match evidence")
    eligible = latency_match.get("eligible_packets")
    matched = latency_match.get("matched_packets")
    coverage = latency_match.get("coverage")
    if (
        isinstance(eligible, bool)
        or not isinstance(eligible, int)
        or eligible <= 0
        or isinstance(matched, bool)
        or not isinstance(matched, int)
        or matched <= 0
        or matched > eligible
        or isinstance(coverage, bool)
        or not isinstance(coverage, (int, float))
        or not math.isfinite(float(coverage))
        or not math.isclose(
            float(coverage),
            matched / eligible,
            rel_tol=1e-12,
            abs_tol=1e-12,
        )
    ):
        raise RuntimeError("run_summary.json has invalid two-tap match evidence")
    class_matches = latency_match.get("classes")
    if not isinstance(class_matches, Mapping) or set(class_matches) != required_classes:
        raise RuntimeError("run_summary.json requires two-tap match evidence for every class")
    class_eligible_total = 0
    class_matched_total = 0
    for class_name in required_classes:
        evidence = class_matches[class_name]
        if not isinstance(evidence, Mapping):
            raise RuntimeError(f"run_summary.json has invalid {class_name} match evidence")
        class_eligible = evidence.get("eligible_packets")
        class_matched = evidence.get("matched_packets")
        class_coverage = evidence.get("coverage")
        if (
            isinstance(class_eligible, bool)
            or not isinstance(class_eligible, int)
            or class_eligible <= 0
            or isinstance(class_matched, bool)
            or not isinstance(class_matched, int)
            or class_matched <= 0
            or class_matched > class_eligible
            or isinstance(class_coverage, bool)
            or not isinstance(class_coverage, (int, float))
            or not math.isfinite(float(class_coverage))
            or not math.isclose(
                float(class_coverage),
                class_matched / class_eligible,
                rel_tol=1e-12,
                abs_tol=1e-12,
            )
        ):
            raise RuntimeError(f"run_summary.json has invalid {class_name} match evidence")
        class_eligible_total += class_eligible
        class_matched_total += class_matched
    if class_eligible_total != eligible or class_matched_total != matched:
        raise RuntimeError("run_summary.json has inconsistent class match totals")
    completion = _required_mapping(raw.get("tool_completion"), "tool_completion")
    completion_count = _positive_integer(completion.get("count"), "tool_completion.count")
    del completion_count
    for metric in ("p50_ms", "p95_ms", "p99_ms"):
        _nonnegative_number(completion.get(metric), f"tool_completion.{metric}")
    attempts = _required_mapping(raw.get("agent_attempts"), "agent_attempts")
    attempted = _positive_integer(attempts.get("attempted"), "agent_attempts.attempted")
    completed = _nonnegative_integer(attempts.get("completed"), "agent_attempts.completed")
    failed = _nonnegative_integer(attempts.get("failed"), "agent_attempts.failed")
    if completed + failed != attempted:
        raise RuntimeError("run_summary.json has inconsistent agent_attempts")
    corpus = _required_mapping(raw.get("corpus"), "corpus")
    verification_rate = _nonnegative_number(
        corpus.get("verification_rate"), "corpus.verification_rate"
    )
    if verification_rate > 1:
        raise RuntimeError("run_summary.json corpus.verification_rate exceeds one")
    measurement_start = _nonnegative_number(raw.get("measurement_start_s"), "measurement_start_s")
    measurement_end = _nonnegative_number(raw.get("measurement_end_s"), "measurement_end_s")
    if measurement_end <= measurement_start:
        raise RuntimeError("run_summary.json has invalid measurement interval")
    telemetry_digests: dict[str, str] = {}
    for telemetry_name in ("packet_telemetry.tsv", "ingress_packet_telemetry.tsv"):
        telemetry_path = run.output_dir / telemetry_name
        if not telemetry_path.is_file():
            raise RuntimeError(f"workload omitted two-tap telemetry: {telemetry_path}")
        try:
            telemetry_digests[telemetry_name] = hashlib.sha256(
                telemetry_path.read_bytes()
            ).hexdigest()
        except OSError as exc:
            raise RuntimeError(f"could not hash two-tap telemetry: {telemetry_path}") from exc
    controller_lifecycle_sha256: str | None = None
    if run.system == "ours":
        lifecycle_path = run.output_dir / "controller_lifecycle.json"
        try:
            lifecycle_bytes = lifecycle_path.read_bytes()
            lifecycle = json.loads(lifecycle_bytes)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("ours run requires controller lifecycle evidence") from exc
        if (
            not isinstance(lifecycle, Mapping)
            or lifecycle.get("ready") is not True
            or lifecycle.get("status") != "stopped"
            or lifecycle.get("workload_succeeded") is not True
            or isinstance(lifecycle.get("policy_version"), bool)
            or not isinstance(lifecycle.get("policy_version"), int)
            or lifecycle["policy_version"] <= 0
        ):
            raise RuntimeError("ours run has invalid controller lifecycle evidence")
        controller_lifecycle_sha256 = hashlib.sha256(lifecycle_bytes).hexdigest()
    return RunArtifactDigests(
        run_summary_sha256=hashlib.sha256(encoded).hexdigest(),
        packet_telemetry_sha256=telemetry_digests["packet_telemetry.tsv"],
        ingress_packet_telemetry_sha256=telemetry_digests["ingress_packet_telemetry.tsv"],
        controller_lifecycle_sha256=controller_lifecycle_sha256,
    )


def _required_mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"run_summary.json requires {field} metrics")
    return cast(Mapping[str, object], value)


def _nonnegative_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(f"run_summary.json {field} must be numeric")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise RuntimeError(f"run_summary.json {field} must be finite and non-negative")
    return number


def _positive_integer(value: object, field: str) -> int:
    integer = _nonnegative_integer(value, field)
    if integer <= 0:
        raise RuntimeError(f"run_summary.json {field} must be positive")
    return integer


def _nonnegative_integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RuntimeError(f"run_summary.json {field} must be a non-negative integer")
    return value


def _run_command(command: tuple[str, ...]) -> None:
    try:
        result = subprocess.run(command, capture_output=True, check=False, text=True)
    except OSError as exc:
        raise RuntimeError(f"could not execute command: {command[0]}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic output"
        raise RuntimeError(f"command failed ({command[0]}): {detail}")


def _write_manifest(path: Path, values: Mapping[str, object]) -> None:
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(values, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _require_mapping(raw: object) -> Mapping[str, object]:
    if not isinstance(raw, Mapping) or not all(isinstance(key, str) for key in raw):
        raise ConfigError("experiment config must be a YAML mapping with string keys")
    return cast(Mapping[str, object], raw)


def _require_string(raw: object, field: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigError(f"{field} must be a non-empty string")
    return raw


def _require_int(raw: object, field: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ConfigError(f"{field} must be an integer")
    return raw


def _require_sequence(raw: object, field: str) -> Sequence[object]:
    if not isinstance(raw, list) or not raw:
        raise ConfigError(f"{field} must be a non-empty YAML list")
    return raw


def _require_int_tuple(raw: object, field: str) -> tuple[int, ...]:
    return tuple(_require_int(item, field) for item in _require_sequence(raw, field))


def _require_string_tuple(raw: object, field: str) -> tuple[str, ...]:
    return tuple(_require_string(item, field) for item in _require_sequence(raw, field))


if __name__ == "__main__":
    raise SystemExit(main())
