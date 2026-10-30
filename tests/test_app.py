"""Controller entrypoint: startup push and fail-fast exit codes."""

import threading
from pathlib import Path
from unittest.mock import patch

from controller import app
from controller.switch_api import SwitchApiError


def test_main_pushes_policy_and_returns_zero() -> None:
    with patch("controller.app.push_policy", return_value=3) as push:
        assert app.main(["--link-mbps", "20"]) == 0
    assert push.call_args.args[1:] == (20, None)


def test_main_returns_one_on_switch_error() -> None:
    with patch("controller.app.push_policy", side_effect=SwitchApiError("down")):
        assert app.main(["--link-mbps", "20"]) == 1


def test_main_returns_one_on_bad_tree_file(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    assert app.main(["--link-mbps", "20", "--compiled-tree", str(missing)]) == 1


def test_daemon_requires_complete_tree_and_cpu_configuration_before_policy_mutation() -> None:
    with patch("controller.app.push_policy") as push:
        assert app.main(["--link-mbps", "20", "--run"]) == 1
    push.assert_not_called()


def test_daemon_wires_receiver_and_service_and_restores_signal_handlers() -> None:
    with (
        patch("controller.app.load_compiled_tree", return_value="tree"),
        patch("controller.app.push_policy", return_value=4),
        patch("controller.app.PuntReceiver") as receiver,
        patch("controller.app.ControllerService") as service,
        patch("controller.app.Reclassifier"),
        patch("controller.app.signal.signal", return_value="previous") as signals,
    ):
        assert (
            app.main(
                [
                    "--link-mbps",
                    "20",
                    "--compiled-tree",
                    "tree.json",
                    "--run",
                    "--cpu-interface",
                    "cpu0",
                    "--upstream-port",
                    "1",
                    "--downstream-port",
                    "4",
                ]
            )
            == 0
        )
    receiver.assert_called_once()
    run_args = service.return_value.run.call_args.args
    assert len(run_args) == 2
    assert isinstance(run_args[0], threading.Event)
    assert run_args[1] is receiver.return_value.__enter__.return_value.failed
    assert signals.call_count == 4
