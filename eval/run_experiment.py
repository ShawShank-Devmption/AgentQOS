"""Validate experiment inputs before the Phase 4 execution engine is added."""

import argparse
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
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
)

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
    if any(burst not in BURST_INTENSITIES for burst in bursts):
        raise ConfigError(f"burst_intensity values must be in {BURST_INTENSITIES}")
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


def main(argv: Sequence[str] | None = None) -> int:
    """Validate one experiment configuration from the command line.

    Args:
        argv: Optional command-line arguments.

    Returns:
        Zero for a valid config, otherwise one.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        config = load_config(args.config)
    except (ConfigError, FileNotFoundError) as exc:
        LOGGER.error("experiment config is invalid: %s", exc)
        return 1
    LOGGER.info(
        "validated experiment %s: system=%s seeds=%d",
        config.name,
        config.system,
        len(config.seeds),
    )
    return 0


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
