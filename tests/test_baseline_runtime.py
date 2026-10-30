"""Executable baseline boundaries: owned network, qdiscs, nginx and adapter prerequisites."""

import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from common.contracts import TrafficClass
from eval.baselines import app_limiter, diffserv, fairq, fifo
from harness.runtime import RuntimeConfig, RuntimePrerequisiteError


def _artifact(path: Path) -> Path:
    path.write_text(json.dumps({"pipelines": [{"tables": [{"name": "tbl_l2_forward"}]}]}))
    return path


def test_fifo_rejects_non_l2_artifact_before_allocating_network(tmp_path: Path) -> None:
    artifact = tmp_path / "wrong.json"
    artifact.write_text('{"pipelines": [{"tables": [{"name": "tbl_tree_l0"}]}]}')
    with pytest.raises(RuntimePrerequisiteError, match="tbl_l2_forward"):
        fifo.validate_pipeline(artifact)


def test_fifo_owns_network_and_cleans_up_after_workload_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = RuntimeConfig(_artifact(tmp_path / "l2.json"), tmp_path / "run")
    network = Mock()
    monkeypatch.setattr(fifo, "validate_prerequisites", lambda config: None)
    monkeypatch.setattr(fifo, "create_network", lambda config: network)
    monkeypatch.setattr(fifo, "wait_for_switch", lambda api: None)
    monkeypatch.setattr(fifo, "install_l2_forwarding", lambda api: None)
    with pytest.raises(ValueError, match="workload"), fifo.running_fifo(config) as owned:
        assert owned is network
        raise ValueError("workload failed")
    network.stop.assert_called_once()
    assert json.loads((config.output_dir / "baseline.json").read_text())["cleanup"] == "passed"


def test_fairq_commands_keep_bottleneck_rate_delay_and_seeded_round_robin() -> None:
    flow = fairq.FlowTuple("10.0.0.1", "10.0.0.100", "tcp", 2000, 80)
    commands = fairq.qdisc_commands(
        "s1-eth4", 20, 1, fairq.FairQueueConfig(7, 4, 128, 1514, (flow,))
    )
    assert [
        "tc",
        "class",
        "add",
        "dev",
        "s1-eth4",
        "parent",
        "1:",
        "classid",
        "1:1",
        "htb",
        "rate",
        "20mbit",
        "ceil",
        "20mbit",
    ] in commands
    assert [
        "tc",
        "qdisc",
        "add",
        "dev",
        "s1-eth4",
        "parent",
        "1:1",
        "handle",
        "10:",
        "netem",
        "delay",
        "1ms",
    ] in commands
    assert any(command[-1] == "drr" for command in commands)
    assert commands[-1][-3:] == ["matchall", "classid", "20:1"]
    marked = next(command for command in commands if "flower" in command)
    assert marked[-3:] == ["80", "classid", "20:2"]
    assert "1514" in next(command for command in commands if "quantum" in command)


def test_fairq_never_accepts_arbitrary_interface_name() -> None:
    with pytest.raises(ValueError, match="interface"):
        fairq.qdisc_commands(
            "eth0; touch /tmp/oops",
            20,
            1,
            fairq.FairQueueConfig(
                7, 4, 128, 1514, (fairq.FlowTuple("10.0.0.1", "10.0.0.100", "tcp", 2000, 80),)
            ),
        )


def test_diffserv_uses_frozen_treatments_and_unknown_is_best_effort() -> None:
    config = diffserv.DiffServConfig(
        (diffserv.PortRule("tcp", 443, TrafficClass.AGENT_INTERACTIVE),)
    )
    assert config.treatment("tcp", 443).dscp == 18
    assert config.treatment("tcp", 443).queue_priority == 1
    assert config.treatment("udp", 443).dscp == 0
    with pytest.raises(ValueError, match="duplicate"):
        diffserv.DiffServConfig((diffserv.PortRule("tcp", 443, TrafficClass.AGENT_BULK),) * 2)


def test_diffserv_rejects_fifo_artifact_without_claiming_same_queues(tmp_path: Path) -> None:
    config = diffserv.DiffServConfig(
        (diffserv.PortRule("tcp", 80, TrafficClass.HUMAN_INTERACTIVE),)
    )
    with pytest.raises(RuntimePrerequisiteError, match="tbl_class_action"):
        config.validate_pipeline(_artifact(tmp_path / "l2.json"))


def test_nginx_config_delays_excess_requests_and_preserves_upstream(tmp_path: Path) -> None:
    config = app_limiter.NginxConfig("127.0.0.1", 8080, "127.0.0.1", 9000, 7, 12, tmp_path)
    text = app_limiter.render_config(config)
    assert "rate=7r/s" in text
    assert "limit_req zone=target burst=12;" in text
    assert "nodelay" not in text
    assert "proxy_pass http://127.0.0.1:9000;" in text
    assert "listen 127.0.0.1:8080;" in text
    assert "limit_req_status 429;" in text


def test_nginx_rejects_directive_injection_and_same_listener(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="IP"):
        app_limiter.NginxConfig("127.0.0.1;}", 8080, "127.0.0.1", 9000, 7, 12, tmp_path)
    with pytest.raises(ValueError, match="upstream"):
        app_limiter.NginxConfig("127.0.0.1", 8080, "127.0.0.1", 8080, 7, 12, tmp_path)


def test_nginx_configuration_is_tested_before_process_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = app_limiter.NginxConfig("127.0.0.1", 8080, "127.0.0.1", 9000, 7, 12, tmp_path)
    host = Mock()
    host.popen.return_value.communicate.return_value = ("", "bad config")
    host.popen.return_value.returncode = 1
    with (
        pytest.raises(RuntimePrerequisiteError, match="bad config"),
        app_limiter.running_nginx(host, config),
    ):
        pytest.fail("must not yield on invalid nginx configuration")
    assert host.popen.call_count == 1
    assert "-t" in host.popen.call_args.args[0]


def test_nginx_stops_only_the_owned_foreground_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = app_limiter.NginxConfig("127.0.0.1", 8080, "127.0.0.1", 9000, 7, 12, tmp_path)
    checker = Mock(returncode=0)
    checker.communicate.return_value = ("", "valid")
    process = Mock()
    process.poll.return_value = None
    host = Mock()
    host.popen.side_effect = [checker, process]
    monkeypatch.setattr(app_limiter, "_wait_for_listener", lambda *args: None)
    with app_limiter.running_nginx(host, config) as child:
        assert child is process
    process.terminate.assert_called_once()
    assert host.popen.call_args_list[1].args[0][-1] == "daemon off; master_process off;"


def test_nginx_checker_timeout_reaps_checker(tmp_path: Path) -> None:
    config = app_limiter.NginxConfig("127.0.0.1", 8080, "127.0.0.1", 9000, 7, 12, tmp_path)
    checker = Mock()
    checker.poll.return_value = None
    checker.communicate.side_effect = subprocess.TimeoutExpired("nginx", 10)
    host = Mock()
    host.popen.return_value = checker
    with pytest.raises(subprocess.TimeoutExpired), app_limiter.running_nginx(host, config):
        pytest.fail("must not yield")
    checker.terminate.assert_called_once()


def test_fairq_seed_changes_mapping_and_tuple_direction_is_explicit() -> None:
    flow = fairq.FlowTuple("10.0.0.1", "10.0.0.100", "tcp", 2000, 80)
    assert fairq.bucket_for(flow, fairq.FairQueueConfig(7, 4, 128, 1514, (flow,))) == 2
    assert fairq.bucket_for(flow, fairq.FairQueueConfig(8, 4, 128, 1514, (flow,))) == 1
    with pytest.raises(ValueError, match="seed"):
        fairq.FairQueueConfig(-1, 4, 128, 1514, ())


def test_fairq_partial_install_failure_destroys_owned_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = RuntimeConfig(_artifact(tmp_path / "l2.json"), tmp_path / "run")
    network = Mock()
    child = network.get.return_value.popen.return_value
    child.communicate.return_value = ("", "qdisc unavailable")
    child.returncode = 1
    monkeypatch.setattr(fifo, "validate_prerequisites", lambda config: None)
    monkeypatch.setattr(fifo, "create_network", lambda config: network)
    monkeypatch.setattr(fifo, "wait_for_switch", lambda api: None)
    monkeypatch.setattr(fifo, "install_l2_forwarding", lambda api: None)
    with (
        pytest.raises(RuntimeError, match="qdisc unavailable"),
        fairq.running_fairq(
            runtime,
            fairq.FairQueueConfig(
                7, 4, 128, 1514, (fairq.FlowTuple("10.0.0.1", "10.0.0.100", "tcp", 2000, 80),)
            ),
        ),
    ):
        pytest.fail("must not yield after failed qdisc install")
    network.stop.assert_called_once()


def test_fifo_retains_workload_error_if_cleanup_also_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = RuntimeConfig(_artifact(tmp_path / "l2.json"), tmp_path / "run")
    network = Mock()
    network.stop.side_effect = RuntimeError("cleanup failed")
    monkeypatch.setattr(fifo, "validate_prerequisites", lambda config: None)
    monkeypatch.setattr(fifo, "create_network", lambda config: network)
    monkeypatch.setattr(fifo, "wait_for_switch", lambda api: None)
    monkeypatch.setattr(fifo, "install_l2_forwarding", lambda api: None)
    with pytest.raises(ValueError, match="workload"), fifo.running_fifo(runtime):
        raise ValueError("workload failed")
    assert json.loads((runtime.output_dir / "baseline.json").read_text())["cleanup"] == "failed"


def test_fairq_requires_planned_flows_instead_of_silently_running_fifo() -> None:
    with pytest.raises(ValueError, match="planned"):
        fairq.FairQueueConfig(7, 4, 128, 1514, ())


def test_real_nginx_accepts_emitted_config_when_installed(tmp_path: Path) -> None:
    import shutil

    nginx = shutil.which("nginx")
    if nginx is None:
        pytest.skip("nginx is unavailable on this host; run this gate in the Linux VM")
    config = app_limiter.NginxConfig("127.0.0.1", 18080, "127.0.0.1", 19000, 7, 12, tmp_path)
    path = tmp_path / "nginx.conf"
    path.write_text(app_limiter.render_config(config))
    completed = subprocess.run(
        [nginx, "-t", "-p", str(tmp_path) + "/", "-c", str(path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("ip", [1, "10.0.0.1; echo bad"])
def test_fairq_rejects_ip_values_that_cannot_be_tc_argv(ip: object) -> None:
    with pytest.raises(ValueError):
        fairq.FlowTuple(ip, "10.0.0.100", "tcp", 2000, 80)  # type: ignore[arg-type]
