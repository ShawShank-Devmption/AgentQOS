"""Controller entrypoint: startup push and fail-fast exit codes."""

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
