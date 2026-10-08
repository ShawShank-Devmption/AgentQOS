"""Tests for deterministic experiment expansion and execution safety."""

import json
from pathlib import Path

import pytest

from eval.baselines.base import baseline_plan
from eval.run_experiment import (
    ExperimentConfig,
    ExperimentLock,
    ExperimentLockedError,
    config_hash,
    execute_config,
    execute_run,
    expand_runs,
)


def _config() -> ExperimentConfig:
    return ExperimentConfig(
        name="test",
        system="fifo",
        topology="choke_v1",
        link_mbps=20,
        agent_share_pct=(10, 90),
        burst_intensity=("low", "high"),
        duration_s=30,
        seeds=(1, 2),
        outputs=Path("results/test"),
    )


def test_config_hash_is_stable_sha256_of_validated_values() -> None:
    assert config_hash(_config()) == (
        "552d18894731f2a38658b587d78ced5701f24c8ccf42d7304eda6c2c2efcacd8"
    )


def test_expand_runs_covers_every_share_burst_and_seed(tmp_path: Path) -> None:
    runs = expand_runs(_config(), tmp_path)

    assert len(runs) == 8
    assert {(run.agent_share_pct, run.burst_intensity, run.seed) for run in runs} == {
        (10, "low", 1),
        (10, "low", 2),
        (10, "high", 1),
        (10, "high", 2),
        (90, "low", 1),
        (90, "low", 2),
        (90, "high", 1),
        (90, "high", 2),
    }
    assert all(run.config_hash == config_hash(_config()) for run in runs)
    assert len({run.output_dir for run in runs}) == 8


def test_host_lock_rejects_a_second_experiment(tmp_path: Path) -> None:
    lock_path = tmp_path / "experiment.lock"
    with (
        ExperimentLock(lock_path),
        pytest.raises(ExperimentLockedError, match="already running"),
        ExperimentLock(lock_path),
    ):
        pass


def test_run_directories_are_append_only(tmp_path: Path) -> None:
    run = expand_runs(_config(), tmp_path)[0]
    execute_run(run, dry_run=True)

    with pytest.raises(FileExistsError):
        execute_run(run, dry_run=True)


def test_config_preflight_rejects_any_existing_cell_before_execution(tmp_path: Path) -> None:
    runs = expand_runs(_config(), tmp_path)
    runs[1].output_dir.mkdir(parents=True)
    commands: list[tuple[str, ...]] = []

    with pytest.raises(FileExistsError, match=str(runs[1].output_dir)):
        execute_config(
            _config(),
            tmp_path,
            tmp_path / "experiment.lock",
            command_runner=commands.append,
        )

    assert commands == []
    assert not runs[0].output_dir.exists()


def test_command_failure_is_recorded_before_it_is_raised(tmp_path: Path) -> None:
    run = expand_runs(_config(), tmp_path)[0]

    def fail_command(command: tuple[str, ...]) -> None:
        raise RuntimeError(f"failed: {command[0]}")

    with pytest.raises(RuntimeError, match="failed"):
        execute_run(run, command_runner=fail_command)

    manifest = json.loads((run.output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"
    assert "failed:" in manifest["error"]


def test_experiment_delegates_baseline_lifecycle_to_topology_aware_workload(
    tmp_path: Path,
) -> None:
    run = expand_runs(_config(), tmp_path)[0]
    commands: list[tuple[str, ...]] = []

    execute_run(run, command_runner=commands.append)

    manifest = json.loads((run.output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "complete"
    assert len(commands) == 1
    assert commands[0][1:3] == ("-m", "harness.demo")
    assert "--execute" in commands[0]


@pytest.mark.parametrize("system", ["fifo", "diffserv", "fairq", "app_limiter"])
def test_each_baseline_has_setup_and_teardown_commands(system: str) -> None:
    plan = baseline_plan(system, link_mbps=20)

    assert plan.name == system
    assert plan.setup_commands
    assert plan.teardown_commands
    assert all(command and all(argument for argument in command) for command in plan.setup_commands)


def test_diffserv_teardown_removes_the_installed_marking_rule() -> None:
    plan = baseline_plan("diffserv", link_mbps=20)

    assert plan.teardown_commands == (("tc", "qdisc", "del", "dev", "s1-eth7", "root"),)


@pytest.mark.parametrize("system", ["fifo", "diffserv", "fairq"])
def test_queue_baselines_preserve_the_configured_bottleneck(system: str) -> None:
    plan = baseline_plan(system, link_mbps=35)
    arguments = tuple(argument for command in plan.setup_commands for argument in command)

    assert "35mbit" in arguments


def test_diffserv_marks_and_demotes_mcp_on_switch_egress() -> None:
    plan = baseline_plan("diffserv", link_mbps=20)
    arguments = tuple(argument for command in plan.setup_commands for argument in command)

    assert "iptables" not in arguments
    assert "dport" in arguments
    assert "8080" in arguments
    assert "dsfield" in arguments
    assert "0x48" in arguments
    assert "10:3" in arguments


def test_fairq_uses_a_flow_hash_queue_below_the_shaper() -> None:
    plan = baseline_plan("fairq", link_mbps=20)

    assert plan.setup_commands[-1] == (
        "tc",
        "qdisc",
        "replace",
        "dev",
        "s1-eth7",
        "parent",
        "1:10",
        "handle",
        "10:",
        "sfq",
        "perturb",
        "10",
    )
