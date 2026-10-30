"""Actual host-command execution and append-only experiment bookkeeping."""

import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from eval import run_experiment as runner


def config() -> runner.ExperimentConfig:
    return runner.ExperimentConfig(
        name="test",
        system="fifo",
        topology="choke_v1",
        link_mbps=20,
        agent_share_pct=(30,),
        burst_intensity=("low",),
        duration_s=1,
        seeds=(2, 1),
        outputs=Path("results/test"),
    )


def test_grid_and_hash_are_deterministic() -> None:
    value = config()
    cells = runner.plan_grid(value)
    assert [(cell.seed, cell.agent_share_pct, cell.burst_intensity) for cell in cells] == [
        (1, 30, "low"),
        (2, 30, "low"),
    ]
    assert runner.config_hash(value) == runner.config_hash(replace(value, seeds=(1, 2)))
    assert runner.config_hash(value) != runner.config_hash(replace(value, link_mbps=30))


def test_host_lock_excludes_other_runs_and_retains_lock_inode(tmp_path: Path) -> None:
    lock = tmp_path / "host.lock"
    with runner.host_lock(lock):
        inode = lock.stat().st_ino
        with pytest.raises(runner.ExperimentBusyError), runner.host_lock(lock):
            pytest.fail("second lock acquired")
    with runner.host_lock(lock):
        assert lock.stat().st_ino == inode


def test_command_receives_frozen_cell_environment_and_preserves_raw(tmp_path: Path) -> None:
    # A tiny real child command records measured fixture samples at the execution boundary.
    command = [
        sys.executable,
        "-c",
        """
import json, os
from pathlib import Path
assert os.environ['AAN_SYSTEM'] == 'fifo'
assert os.environ['AAN_AGENT_SHARE_PCT'] == '30'
seed = int(os.environ['AAN_SEED'])
raw = {'measurement_start_s': 0, 'measurement_end_s': 1,
       'packets': [{'packet_id': str(seed), 'traffic_class': 1,
                    'sent_s': 0, 'received_s': 0.01, 'size_bytes': 100}],
       'tool_calls': [], 'classifications': []}
Path(os.environ['AAN_OBSERVATIONS_PATH']).write_text(json.dumps(raw))
""",
    ]
    paths = runner.execute_experiment(config(), command, tmp_path, tmp_path / "host.lock")
    assert len(paths) == 2
    for path in paths:
        assert (path / "observations.json").exists()
        assert (path / "config.yaml").exists()
        started = json.loads((path / "started.json").read_text())
        completed = json.loads((path / "completed.json").read_text())
        assert started["seed"] in (1, 2)
        assert started["command"] == command
        assert completed["status"] == "success"
        assert completed["returncode"] == 0
        assert (path / "summary.json").exists()
    original = (paths[0] / "started.json").read_bytes()
    with pytest.raises(runner.RunExistsError):
        runner.execute_experiment(config(), command, tmp_path, tmp_path / "host.lock")
    assert (paths[0] / "started.json").read_bytes() == original


@pytest.mark.parametrize(
    "command",
    [
        [sys.executable, "-c", "raise SystemExit(7)"],
        [sys.executable, "-c", "pass"],
        ["/definitely/missing/experiment-command"],
    ],
)
def test_failed_or_unmeasured_runs_leave_durable_failure_metadata(
    tmp_path: Path, command: list[str]
) -> None:
    with pytest.raises(runner.ExperimentExecutionError):
        runner.execute_experiment(config(), command, tmp_path, tmp_path / "host.lock")
    manifests = list(tmp_path.glob("results/test/**/completed.json"))
    assert len(manifests) == 1
    assert json.loads(manifests[0].read_text())["status"] == "failed"
    assert (manifests[0].parent / "started.json").exists()
    assert not (manifests[0].parent / "summary.json").exists()


@pytest.mark.parametrize(
    "field,value", [("agent_share_pct", [30, 30]), ("burst_intensity", ["low", "low"])]
)
def test_duplicate_cells_in_yaml_are_rejected(tmp_path: Path, field: str, value: object) -> None:
    import yaml

    raw = {
        "name": "test",
        "system": "fifo",
        "topology": "choke_v1",
        "link_mbps": 20,
        "agent_share_pct": [30],
        "burst_intensity": ["low"],
        "duration_s": 1,
        "seeds": [1],
        "outputs": "results/test",
    }
    raw[field] = value
    source = tmp_path / "config.yaml"
    source.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(runner.ConfigError, match="unique"):
        runner.load_config(source)


def test_duplicate_yaml_keys_are_rejected(tmp_path: Path) -> None:
    source = tmp_path / "config.yaml"
    source.write_text("name: a\nname: b\n", encoding="utf-8")
    with pytest.raises(runner.ConfigError, match="duplicate"):
        runner.load_config(source)


def test_direct_config_api_cannot_escape_output_or_cell_paths(tmp_path: Path) -> None:
    unsafe = replace(config(), burst_intensity=("../../outside",))
    with pytest.raises(runner.ConfigError):
        runner.execute_experiment(
            unsafe, [sys.executable, "-c", "pass"], tmp_path, tmp_path / "host.lock"
        )
    assert not (tmp_path / "results").exists()
