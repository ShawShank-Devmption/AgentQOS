"""Plan and execute measured experiments with exclusive, append-only host bookkeeping."""

import argparse
import fcntl
import hashlib
import json
import logging
import os
import platform
import signal
import subprocess
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

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
)
from eval.metrics import load_observations, summarize_observations, write_json

LOGGER = logging.getLogger(__name__)


class ConfigError(ValueError):
    """Raised when an experiment configuration violates design section 4.6."""


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
        raw = yaml.load(config_path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ConfigError(f"could not parse experiment config: {config_path}") from exc

    return validate_config(raw)


class _UniqueKeyLoader(yaml.SafeLoader):
    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        keys = [self.construct_object(key, deep=deep) for key, _ in node.value]
        if any(not isinstance(key, str) for key in keys):
            raise ConfigError("experiment fields must be strings")
        if len(set(keys)) != len(keys):
            raise ConfigError("duplicate YAML experiment field")
        return super().construct_mapping(node, deep=deep)


def validate_config(raw: object) -> ExperimentConfig:
    """Validate frozen-schema values from YAML or an in-memory configuration snapshot."""
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
    if any(burst not in BURST_INTENSITIES for burst in bursts):
        raise ConfigError(f"burst_intensity values must be in {BURST_INTENSITIES}")
    if any(seed < 0 for seed in seeds):
        raise ConfigError("seeds must be non-negative")
    if len(set(seeds)) != len(seeds):
        raise ConfigError("seeds must be unique")
    if len(set(shares)) != len(shares) or len(set(bursts)) != len(bursts):
        raise ConfigError("agent_share_pct and burst_intensity values must be unique")

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


def main(argv: Sequence[str] | None = None) -> int:
    """Validate one experiment configuration from the command line.

    Args:
        argv: Optional command-line arguments.

    Returns:
        Zero for a valid config, otherwise one.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--plan", action="store_true", help="log the deterministic grid")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--execute", nargs=argparse.REMAINDER, help="real workload argv; no shell")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        config = load_config(args.config)
        if args.execute is not None:
            execute_experiment(config, args.execute, args.project_root)
        elif args.plan:
            for cell in plan_grid(config):
                LOGGER.info("planned %s/%s", config_hash(config), cell.relative_path)
    except (ConfigError, OSError, ExperimentExecutionError) as exc:
        LOGGER.error("experiment config is invalid: %s", exc)
        return 1
    LOGGER.info(
        "validated experiment %s: system=%s seeds=%d",
        config.name,
        config.system,
        len(config.seeds),
    )
    return 0


class ExperimentExecutionError(RuntimeError):
    """Raised when a measured run cannot complete safely."""


class ExperimentBusyError(ExperimentExecutionError):
    """Raised when another process owns this host's experiment lock."""


class RunExistsError(ExperimentExecutionError):
    """Raised instead of overwriting a prior successful, failed or interrupted run."""


@dataclass(frozen=True)
class ExperimentCell:
    """One deterministic workload point and independent random seed."""

    seed: int
    agent_share_pct: int
    burst_intensity: str

    @property
    def relative_path(self) -> Path:
        """Return the stable seed/cell directory below a configuration hash."""
        return Path(f"seed-{self.seed}") / (
            f"share-{self.agent_share_pct}_burst-{self.burst_intensity}"
        )


def config_values(config: ExperimentConfig) -> dict[str, Any]:
    """Return canonical frozen-schema values, sorting set-like grid dimensions."""
    return {
        "name": config.name,
        "system": config.system,
        "topology": config.topology,
        "link_mbps": config.link_mbps,
        "agent_share_pct": sorted(config.agent_share_pct),
        "burst_intensity": sorted(config.burst_intensity),
        "duration_s": config.duration_s,
        "seeds": sorted(config.seeds),
        "outputs": config.outputs.as_posix(),
    }


def config_hash(config: ExperimentConfig) -> str:
    """Return SHA-256 over canonical configuration values, independent of YAML formatting."""
    encoded = json.dumps(config_values(config), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def plan_grid(config: ExperimentConfig) -> tuple[ExperimentCell, ...]:
    """Enumerate every config-driven seed/share/burst cell in deterministic order."""
    return tuple(
        ExperimentCell(seed, share, burst)
        for seed in sorted(config.seeds)
        for share in sorted(config.agent_share_pct)
        for burst in sorted(config.burst_intensity)
    )


@contextmanager
def host_lock(path: Path | None = None) -> Iterator[None]:
    """Hold a nonblocking host-wide lock; a retained inode prevents unlink races."""
    lock_path = path if path is not None else Path("/tmp/agent-aware-networking-experiment.lock")
    # §7.19: a fixed host-wide inode serializes all configs and output destinations.
    with lock_path.open("a+", encoding="utf-8") as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ExperimentBusyError(f"another experiment owns host lock {lock_path}") from exc
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _invoke(
    command: Sequence[str], environment: dict[str, str], root: Path, output: Path, timeout_s: int
) -> int:
    with (output / "stdout.log").open("x") as stdout, (output / "stderr.log").open("x") as stderr:
        process = subprocess.Popen(
            list(command),
            cwd=root,
            env=environment,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
        try:
            return process.wait(timeout=timeout_s)
        except BaseException:
            # The workload owns its runtime cleanup; stop only this invocation's process group.
            LOGGER.exception("stopping interrupted experiment process group %d", process.pid)
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                LOGGER.debug("experiment process already exited: %d", process.pid)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            raise


def _execute_cell(
    config: ExperimentConfig, cell: ExperimentCell, command: Sequence[str], root: Path, digest: str
) -> Path:
    output = root / config.outputs / digest / cell.relative_path
    try:
        # §7.19: exclusive directory creation prevents retries from editing previous evidence.
        output.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise RunExistsError(f"run already exists; refusing overwrite: {output}") from exc
    started = {
        "status": "started",
        "started_at": _timestamp(),
        "config_hash": digest,
        "config": config_values(config),
        "seed": cell.seed,
        "agent_share_pct": cell.agent_share_pct,
        "burst_intensity": cell.burst_intensity,
        "command": list(command),
        "host": platform.node(),
        "platform": platform.platform(),
        "python": platform.python_version(),
    }
    returncode: int | None = None
    try:
        write_json(output / "started.json", started)
        with (output / "config.yaml").open("x", encoding="utf-8") as stream:
            yaml.safe_dump(config_values(config), stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        environment = dict(os.environ)
        environment.update(
            {
                "AAN_CONFIG_PATH": str(output / "config.yaml"),
                "AAN_CONFIG_HASH": digest,
                "AAN_SYSTEM": config.system,
                "AAN_TOPOLOGY": config.topology,
                "AAN_LINK_MBPS": str(config.link_mbps),
                "AAN_SEED": str(cell.seed),
                "AAN_AGENT_SHARE_PCT": str(cell.agent_share_pct),
                "AAN_BURST_INTENSITY": cell.burst_intensity,
                "AAN_DURATION_S": str(config.duration_s),
                "AAN_OUTPUT_DIR": str(output),
                "AAN_OBSERVATIONS_PATH": str(output / "observations.json"),
            }
        )
        returncode = _invoke(command, environment, root, output, config.duration_s + 60)
        if returncode != 0:
            raise ExperimentExecutionError(f"workload returned status {returncode}")
        raw = load_observations(output / "observations.json")
        write_json(output / "summary.json", summarize_observations(raw))
    except BaseException as exc:
        LOGGER.error("experiment failed at %s: %s", output, exc)
        write_json(
            output / "completed.json",
            {
                "status": "failed",
                "finished_at": _timestamp(),
                "returncode": returncode,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise ExperimentExecutionError(f"run failed; evidence retained at {output}: {exc}") from exc
    write_json(
        output / "completed.json",
        {
            "status": "success",
            "finished_at": _timestamp(),
            "returncode": returncode,
            "observations_sha256": hashlib.sha256(
                (output / "observations.json").read_bytes()
            ).hexdigest(),
        },
    )
    LOGGER.info("measured run complete: %s", output)
    return output


def execute_experiment(
    config: ExperimentConfig,
    command: Sequence[str],
    project_root: Path,
    lock_path: Path | None = None,
) -> tuple[Path, ...]:
    """Run real workload argv for every cell under the host lock and retain all evidence.

    The executable must consume AAN_* cell environment variables, configure the requested real
    system, and write observations.json. No simulator or implicit baseline implementation is
    selected from a system name. Success requires a zero exit status and valid measurements.
    """
    config = validate_config(config_values(config))
    if (
        isinstance(command, str)
        or not command
        or any(not isinstance(arg, str) or not arg for arg in command)
    ):
        raise ExperimentExecutionError("a nonempty real workload argv is required")
    root = project_root.resolve(strict=True)
    output_root = (root / config.outputs).resolve()
    if not output_root.is_relative_to(root / "results"):
        raise ExperimentExecutionError("output path must resolve below project results/")
    with host_lock(lock_path):
        digest = config_hash(config)
        return tuple(
            _execute_cell(config, cell, command, root, digest) for cell in plan_grid(config)
        )


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
