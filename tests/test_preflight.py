"""Tests for Linux experiment readiness checks."""

from pathlib import Path

from eval.preflight import RuntimeFacts, check_preconditions
from eval.run_experiment import ExperimentConfig, expand_runs


def _config(system: str = "fifo") -> ExperimentConfig:
    return ExperimentConfig(
        name="sanity",
        system=system,
        topology="choke_v1",
        link_mbps=20,
        agent_share_pct=(30,),
        burst_intensity=("low",),
        duration_s=30,
        seeds=(1,),
        outputs=Path("results/sanity"),
    )


def _facts(*, commands: frozenset[str] | None = None) -> RuntimeFacts:
    return RuntimeFacts(
        platform="linux",
        effective_uid=0,
        available_commands=commands
        or frozenset(
            {
                "simple_switch",
                "simple_switch_CLI",
                "tshark",
                "iperf3",
                "tc",
            }
        ),
        mininet_available=True,
    )


def test_baseline_preflight_passes_with_linux_runtime_and_artifacts(tmp_path: Path) -> None:
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "l2fwd.json").write_text("{}", encoding="utf-8")
    (tmp_path / "harness" / "tasks").mkdir(parents=True)
    (tmp_path / "harness" / "tasks" / "storm.json").write_text("[]", encoding="utf-8")

    report = check_preconditions(_config(), tmp_path, _facts())

    assert report.ready
    assert not report.failures


def test_preflight_reports_host_command_artifact_and_output_blockers(tmp_path: Path) -> None:
    output_dir = expand_runs(_config(), tmp_path)[0].output_dir
    output_dir.mkdir(parents=True)
    facts = RuntimeFacts(
        platform="darwin",
        effective_uid=501,
        available_commands=frozenset({"iperf3"}),
        mininet_available=False,
    )

    report = check_preconditions(_config(), tmp_path, facts)

    assert not report.ready
    names = {check.name for check in report.failures}
    assert names == {
        "linux",
        "root",
        "commands",
        "mininet",
        "p4-json",
        "task-script",
        "append-only-output",
    }


def test_app_limiter_preflight_requires_nginx(tmp_path: Path) -> None:
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "l2fwd.json").write_text("{}", encoding="utf-8")
    (tmp_path / "harness" / "tasks").mkdir(parents=True)
    (tmp_path / "harness" / "tasks" / "storm.json").write_text("[]", encoding="utf-8")

    report = check_preconditions(_config("app_limiter"), tmp_path, _facts())

    assert {check.name for check in report.failures} == {"commands"}


def test_ours_preflight_requires_dev_a_and_dev_b_artifacts(tmp_path: Path) -> None:
    (tmp_path / "harness" / "tasks").mkdir(parents=True)
    (tmp_path / "harness" / "tasks" / "storm.json").write_text("[]", encoding="utf-8")

    report = check_preconditions(_config("ours"), tmp_path, _facts())

    assert not report.ready
    missing = next(check for check in report.failures if check.name == "ours-integration")
    assert "p4src/agent_aware.p4" in missing.detail
    assert "controller/policy.py" in missing.detail
