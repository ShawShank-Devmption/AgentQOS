"""Frozen baseline sanity configs remain valid independently of runtime prerequisites."""

from pathlib import Path

from common.contracts import EXPERIMENT_SYSTEMS
from eval.run_experiment import load_config


def test_each_system_has_a_valid_sanity_config() -> None:
    root = Path(__file__).resolve().parents[1] / "eval/configs"
    for system in EXPERIMENT_SYSTEMS:
        config = load_config(root / f"sanity_{system}.yaml")
        assert config.system == system
        assert config.duration_s == 30
        assert len(config.seeds) >= 5
