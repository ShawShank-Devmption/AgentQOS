"""C4/P4.3: fixed-rate schedules and measured switch diagnostics."""

import json
from pathlib import Path

import pytest

from common.contracts import CPU_SWITCH_INTERFACE
from eval.overhead import build_overhead_plan, read_cpu_ticks, read_punt_packets
from eval.workload import load_profile
from harness.runtime import RuntimeConfig, switch_command


def test_pin_only_switch_without_enabling_console_logging(tmp_path: Path) -> None:
    config = RuntimeConfig(tmp_path / "pipeline.json", tmp_path / "out", switch_cpu=2)
    command = switch_command(config, [(1, "s1-eth1")], cpu_enabled=False)
    assert command[:4] == ["taskset", "--cpu-list", "2", "simple_switch"]
    assert "--log-console" not in command
    for invalid in (-1, True, 1.5):
        with pytest.raises(ValueError, match="switch_cpu"):
            RuntimeConfig(config.pipeline_json, config.output_dir, switch_cpu=invalid)


def test_fixed_rate_schedule_is_seeded_and_does_not_mutate_profile() -> None:
    profile = load_profile(Path("eval/configs/workload_runtime.json"))
    original = json.dumps(profile, sort_keys=True)
    plan = build_overhead_plan(
        profile, pps=100, seed=1, share=30, burst="low", duration=30, link_mbps=20
    )
    assert plan == build_overhead_plan(
        profile, pps=100, seed=1, share=30, burst="low", duration=30, link_mbps=20
    )
    count = sum(len(flow["offsets_s"]) for flow in plan["flows"])
    assert abs(count / (30 - profile["drain_s"]) - 100) < 1
    assert json.dumps(profile, sort_keys=True) == original
    for invalid in (0, -1, True):
        with pytest.raises(ValueError, match="pps"):
            build_overhead_plan(
                profile, pps=invalid, seed=1, share=30, burst="low", duration=30, link_mbps=20
            )


def test_proc_stat_handles_spaces_and_parentheses_in_process_name(tmp_path: Path) -> None:
    process = tmp_path / "123"
    process.mkdir()
    fields = ["S"] + ["0"] * 10 + ["21", "34"]
    (process / "stat").write_text("123 (simple switch (worker)) " + " ".join(fields))
    assert read_cpu_ticks(123, tmp_path) == 55
    (process / "stat").write_text("malformed")
    with pytest.raises(ValueError, match="stat"):
        read_cpu_ticks(123, tmp_path)


def test_cpu_port_counter_is_switch_tx_and_absent_for_fifo(tmp_path: Path) -> None:
    assert read_punt_packets(False, tmp_path) is None
    counter = tmp_path / CPU_SWITCH_INTERFACE / "statistics" / "tx_packets"
    counter.parent.mkdir(parents=True)
    counter.write_text("17\n")
    assert read_punt_packets(True, tmp_path) == 17
    counter.unlink()
    with pytest.raises(FileNotFoundError):
        read_punt_packets(True, tmp_path)


def test_footprint_counts_bits_without_rounding_each_register_to_bytes(tmp_path: Path) -> None:
    from eval.overhead import pipeline_footprint

    artifact = tmp_path / "pipeline.json"
    artifact.write_text(
        json.dumps(
            {
                "register_arrays": [{"size": 10, "bitwidth": 1}, {"size": 4, "bitwidth": 32}],
                "pipelines": [{"tables": [{"max_size": 64}, {"max_size": 128}]}],
            }
        )
    )
    result = pipeline_footprint(artifact)
    assert result["register_bits"] == 138
    assert result["table_capacity_entries"] == 192


@pytest.mark.parametrize("cpu_available,taskset", [(False, "/bin/taskset"), (True, None)])
def test_missing_cpu_or_taskset_fails_before_allocating_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cpu_available: bool, taskset: str | None
) -> None:
    from harness.runtime import RuntimePrerequisiteError, validate_prerequisites

    monkeypatch.setattr("harness.runtime.platform.system", lambda: "Linux")
    monkeypatch.setattr("harness.runtime.os.geteuid", lambda: 0)
    monkeypatch.setattr(
        "harness.runtime.os.sched_getaffinity",
        lambda _: {2} if cpu_available else {0},
        raising=False,
    )
    monkeypatch.setattr("harness.runtime.shutil.which", lambda _: taskset)
    with pytest.raises(RuntimePrerequisiteError, match="available CPU"):
        validate_prerequisites(
            RuntimeConfig(tmp_path / "pipeline.json", tmp_path / "out", switch_cpu=2)
        )


@pytest.mark.parametrize("system", ["fifo", "ours"])
def test_overhead_cell_retains_observations_cpu_and_punt_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, system: str
) -> None:
    from contextlib import contextmanager
    from unittest.mock import Mock

    from eval import overhead

    output = tmp_path / "cell"
    output.mkdir()
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text('{"register_arrays": [], "pipelines": []}')
    binary = tmp_path / "simple_switch"
    binary.write_bytes(b"binary for hash")
    for name, value in {
        "AAN_SYSTEM": system,
        "AAN_OUTPUT_DIR": str(output),
        "AAN_SEED": "1",
        "AAN_AGENT_SHARE_PCT": "30",
        "AAN_BURST_INTENSITY": "low",
        "AAN_DURATION_S": "30",
        "AAN_LINK_MBPS": "20",
        "AAN_COMPILED_TREE": "tree.json",
        "AAN_OBSERVATIONS_PATH": str(output / "observations.json"),
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr("sys.argv", ["overhead", "--pps", "100", "--switch-cpu", "2"])
    network = Mock()
    network.get.return_value._bmv2.pid = 123
    network.get.return_value._bmv2.args = ["taskset", "--cpu-list", "2", "simple_switch"]
    network.get.return_value._bmv2.poll.return_value = None

    @contextmanager
    def context(*args: object) -> object:
        (output / "network").mkdir()
        (output / "network/simple-switch.log").write_text("")
        yield network

    monkeypatch.setattr(overhead, "network_context", context)
    monkeypatch.setattr(overhead, "archive_inputs", lambda *args: None)
    monkeypatch.setattr(overhead, "pipeline_footprint", lambda _: {})
    monkeypatch.setattr(overhead.shutil, "which", lambda _: str(binary))
    monkeypatch.setattr(overhead.os, "sched_getaffinity", lambda _: {2}, raising=False)
    monkeypatch.setattr(overhead.os, "sysconf", lambda _: 100)
    ticks = iter([200, 1200])
    monkeypatch.setattr(overhead, "read_cpu_ticks", lambda _: next(ticks))
    punts = iter([10, 30] if system == "ours" else [None, None])
    monkeypatch.setattr(overhead, "read_punt_packets", lambda _: next(punts))
    times = iter([100.0, 120.0])
    monkeypatch.setattr(overhead.time, "monotonic", lambda: next(times))
    raw = {"packets": []}
    monkeypatch.setattr(overhead, "_run_network_traffic", lambda *args: raw)
    assert overhead.main() == 0
    assert json.loads((output / "observations.json").read_text()) == raw
    diagnostic = json.loads((output / "overhead.json").read_text())
    assert diagnostic["switch_cpu_pct"] == 50.0
    assert diagnostic["cpu_port_tx_packets"] == (20 if system == "ours" else None)
    assert diagnostic["punt_cap_exceeded"] == (False if system == "ours" else None)
