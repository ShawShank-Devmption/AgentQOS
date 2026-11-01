"""C0: installed PTF does not turn the plain pytest gate into a live-switch run."""

import os
import subprocess
import sys
from pathlib import Path


def _environment(tmp_path: Path, fail_on_import: bool) -> dict[str, str]:
    package = tmp_path / "ptf"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "testutils.py").write_text("")
    (package / "base_tests.py").write_text(
        "raise RuntimeError('PTF runner must own suite imports')\n"
        if fail_on_import
        else "from unittest import TestCase as BaseTest\n"
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join((str(tmp_path), str(Path.cwd())))
    return environment


def test_pytest_does_not_import_ptf_suites_even_when_ptf_is_installed(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "tests"],
        env=_environment(tmp_path, True),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "tests/ptf/" not in result.stdout
    assert "PTF runner must own suite imports" not in result.stdout + result.stderr


def test_runner_can_still_import_every_ptf_suite_directly(tmp_path: Path) -> None:
    script = (
        "import importlib; "
        "[importlib.import_module('tests.ptf.' + name) for name in "
        "('test_parser','test_agent_pipeline','test_diffserv','test_gateway','test_qos')]"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=_environment(tmp_path, False),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
