"""Tests for the frozen experiment configuration trust boundary."""

from pathlib import Path

import pytest
import yaml

from eval.run_experiment import ConfigError, ExperimentConfig, load_config

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_review_smoke_config_is_valid() -> None:
    config = load_config(PROJECT_ROOT / "eval/configs/review_1_smoke.yaml")
    assert config == ExperimentConfig(
        name="review_1_smoke",
        system="ours",
        topology="choke_v1",
        link_mbps=20,
        agent_share_pct=(30,),
        burst_intensity=("low",),
        duration_s=30,
        seeds=(1,),
        outputs=Path("results/review_1_smoke"),
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("system", "unknown", "system must be one of"),
        ("topology", "other", "topology must be choke_v1"),
        ("link_mbps", 9, "link_mbps must be between"),
        ("agent_share_pct", [5], "agent_share_pct values must be between"),
        ("burst_intensity", ["extreme"], "burst_intensity values must be in"),
        ("duration_s", 0, "duration_s must be positive"),
        ("seeds", [1, 1], "seeds must be unique"),
        ("outputs", "/tmp/results", "relative path below results"),
        ("outputs", "results/../outside", "relative path below results"),
    ],
)
def test_invalid_values_are_rejected(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    raw = _valid_config()
    raw[field] = value
    config_path = _write_config(tmp_path, raw)
    with pytest.raises(ConfigError, match=message):
        load_config(config_path)


def test_unknown_fields_are_rejected(tmp_path: Path) -> None:
    raw = _valid_config()
    raw["unplanned_option"] = True
    with pytest.raises(ConfigError, match="unknown experiment fields"):
        load_config(_write_config(tmp_path, raw))


def _valid_config() -> dict[str, object]:
    return {
        "name": "test",
        "system": "fifo",
        "topology": "choke_v1",
        "link_mbps": 20,
        "agent_share_pct": [10, 90],
        "burst_intensity": ["low", "high"],
        "duration_s": 30,
        "seeds": [1, 2],
        "outputs": "results/test/",
    }


def _write_config(tmp_path: Path, raw: dict[str, object]) -> Path:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return config_path
