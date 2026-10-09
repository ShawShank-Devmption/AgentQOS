"""Tests for deterministic experiment expansion and execution safety."""

import json
from pathlib import Path

import pytest

from eval.baselines.base import baseline_plan
from eval.run_experiment import (
    ExperimentConfig,
    ExperimentLock,
    ExperimentLockedError,
    RunSpec,
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


def test_successful_command_without_a_run_summary_is_recorded_as_failed(tmp_path: Path) -> None:
    run = expand_runs(_config(), tmp_path)[0]

    with pytest.raises(RuntimeError, match="run_summary"):
        execute_run(run, command_runner=lambda command: None)

    manifest = json.loads((run.output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"


def test_experiment_delegates_baseline_lifecycle_to_topology_aware_workload(
    tmp_path: Path,
) -> None:
    run = expand_runs(_config(), tmp_path)[0]
    commands: list[tuple[str, ...]] = []

    def complete_command(command: tuple[str, ...]) -> None:
        commands.append(command)
        _write_run_summary(run.output_dir / "run_summary.json", run)

    execute_run(run, command_runner=complete_command)

    manifest = json.loads((run.output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "complete"
    assert len(commands) == 1
    assert commands[0][1:3] == ("-m", "harness.demo")
    assert "--execute" in commands[0]
    assert manifest["run_summary_sha256"]
    assert len(manifest["packet_telemetry_sha256"]) == 64
    assert len(manifest["ingress_packet_telemetry_sha256"]) == 64


def test_non_two_tap_run_summary_is_recorded_as_failed(tmp_path: Path) -> None:
    run = expand_runs(_config(), tmp_path)[0]

    def write_invalid_summary(command: tuple[str, ...]) -> None:
        del command
        _write_run_summary(run.output_dir / "run_summary.json", run)
        summary_path = run.output_dir / "run_summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["latency_method"] = "tcp_ack_rtt"
        summary_path.write_text(json.dumps(summary), encoding="utf-8")

    with pytest.raises(RuntimeError, match="matched two-tap"):
        execute_run(run, command_runner=write_invalid_summary)

    manifest = json.loads((run.output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"


def test_empty_metric_objects_cannot_mark_a_run_complete(tmp_path: Path) -> None:
    run = expand_runs(_config(), tmp_path)[0]

    def write_invalid_summary(command: tuple[str, ...]) -> None:
        del command
        _write_run_summary(run.output_dir / "run_summary.json", run)
        summary_path = run.output_dir / "run_summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["tool_completion"] = {}
        summary_path.write_text(json.dumps(summary), encoding="utf-8")

    with pytest.raises(RuntimeError, match="tool_completion"):
        execute_run(run, command_runner=write_invalid_summary)

    manifest = json.loads((run.output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"


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


def test_app_limiter_plan_runs_nginx_inside_the_target_namespace(tmp_path: Path) -> None:
    plan = baseline_plan("app_limiter", link_mbps=20, output_dir=tmp_path)
    setup = plan.setup_commands[0]

    assert setup[0] == "nginx"
    assert setup[setup.index("-p") + 1] == str(tmp_path / "nginx")
    assert setup[setup.index("-g") + 1] == "daemon off;"
    assert "docker" not in setup
    assert plan.teardown_commands[0][0] == "nginx"


def _write_run_summary(path: Path, run: RunSpec) -> None:
    path.write_text(
        json.dumps(
            {
                "system": run.system,
                "link_mbps": run.link_mbps,
                "agent_share_pct": run.agent_share_pct,
                "burst_intensity": run.burst_intensity,
                "duration_s": run.duration_s,
                "seed": run.seed,
                "latency_method": "matched_two_tap",
                "latency_match": {
                    "eligible_packets": 9,
                    "matched_packets": 9,
                    "coverage": 1.0,
                    "classes": {
                        class_name: {
                            "eligible_packets": 3,
                            "matched_packets": 3,
                            "coverage": 1.0,
                        }
                        for class_name in (
                            "HUMAN_INTERACTIVE",
                            "AGENT_INTERACTIVE",
                            "AGENT_BULK",
                        )
                    },
                },
                "classes": {
                    class_name: {
                        "throughput_mbps": 1.0,
                        "p50_ms": 1.0,
                        "p95_ms": 2.0,
                        "p99_ms": 3.0,
                    }
                    for class_name in (
                        "HUMAN_INTERACTIVE",
                        "AGENT_INTERACTIVE",
                        "AGENT_BULK",
                    )
                },
                "tool_completion": {"count": 1, "p50_ms": 1.0, "p95_ms": 2.0, "p99_ms": 3.0},
                "agent_attempts": {"attempted": 1, "completed": 1, "failed": 0},
                "corpus": {"verification_rate": 1.0},
                "measurement_start_s": 10.0,
                "measurement_end_s": 40.0,
            }
        ),
        encoding="utf-8",
    )
    path.with_name("packet_telemetry.tsv").write_text("target\n", encoding="utf-8")
    path.with_name("ingress_packet_telemetry.tsv").write_text("ingress\n", encoding="utf-8")
