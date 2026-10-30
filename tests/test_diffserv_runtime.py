"""P4.1 static-port DiffServ baseline contract tests."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from common.contracts import TrafficClass
from eval.baselines import diffserv, fifo
from eval.baselines.diffserv import DiffServConfig, PortRule
from harness.runtime import RuntimeConfig, RuntimePrerequisiteError


def _artifact(path: Path, tables: list[str]) -> Path:
    path.write_text(
        json.dumps(
            {
                "pipelines": [{"tables": [{"name": name} for name in tables]}],
                "meter_arrays": [{"name": "M_AI"}, {"name": "M_AB"}],
                "register_arrays": [{"name": "reg_congestion_flag"}],
            }
        )
    )
    return path


def test_diffserv_accepts_only_compiled_port_and_class_action_tables(tmp_path: Path) -> None:
    config = DiffServConfig((PortRule("tcp", 443, TrafficClass.AGENT_INTERACTIVE),))
    config.validate_pipeline(
        _artifact(
            tmp_path / "valid.json", ["tbl_l2_forward", "tbl_diffserv_port", "tbl_class_action"]
        )
    )
    with pytest.raises(RuntimePrerequisiteError):
        config.validate_pipeline(
            _artifact(tmp_path / "missing.json", ["tbl_l2_forward", "tbl_class_action"])
        )
    no_meter = tmp_path / "no_meter.json"
    source = json.loads((tmp_path / "valid.json").read_text())
    source["meter_arrays"] = []
    no_meter.write_text(json.dumps(source))
    with pytest.raises(RuntimePrerequisiteError, match="meter"):
        config.validate_pipeline(no_meter)


def test_diffserv_rule_commands_use_transport_protocol_and_frozen_class_ids() -> None:
    config = DiffServConfig(
        (
            PortRule("tcp", 443, TrafficClass.HUMAN_INTERACTIVE),
            PortRule("udp", 443, TrafficClass.AGENT_BULK),
        )
    )
    assert config.port_rule_commands() == [
        "table_set_default tbl_diffserv_port set_diffserv_class 0",
        "table_clear tbl_diffserv_port",
        "table_add tbl_diffserv_port set_diffserv_class 6 443 => 1",
        "table_add tbl_diffserv_port set_diffserv_class 17 443 => 3",
    ]


def test_diffserv_rejects_non_rules_at_config_boundary() -> None:
    with pytest.raises(ValueError, match="PortRule"):
        DiffServConfig(("tcp:443",))  # type: ignore[arg-type]


def test_diffserv_installs_static_rules_on_owned_network_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = _artifact(
        tmp_path / "diffserv.json",
        ["tbl_l2_forward", "tbl_diffserv_port", "tbl_class_action"],
    )
    runtime = RuntimeConfig(artifact, tmp_path / "run")
    config = DiffServConfig((PortRule("tcp", 443, TrafficClass.HUMAN_INTERACTIVE),))
    network = Mock()
    api = Mock()
    monkeypatch.setattr(fifo, "validate_prerequisites", lambda config: None)
    monkeypatch.setattr(fifo, "create_network", lambda config: network)
    monkeypatch.setattr(fifo, "wait_for_switch", lambda api: None)
    monkeypatch.setattr(fifo, "install_l2_forwarding", lambda api: None)
    monkeypatch.setattr(fifo, "SwitchApi", lambda **kwargs: api)
    with diffserv.running_diffserv(runtime, config) as owned:
        assert owned is network
    network.stop.assert_called_once()
    installed = [command for call in api.execute.call_args_list for command in call.args[0]]
    assert "table_add tbl_diffserv_port set_diffserv_class 6 443 => 1" in installed
    assert "table_add tbl_class_action set_class_action 1 => 34 2 0 48" in installed
    assert json.loads((runtime.output_dir / "baseline.json").read_text())["installed"] is True
