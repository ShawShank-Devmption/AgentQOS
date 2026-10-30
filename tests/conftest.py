"""Shared pytest isolation for host-wide lock files."""

import tempfile
from pathlib import Path

import pytest

# C0/P3.16: only the PTF runner owns live switch parameters/dataplane setup.
# PTF imports suites directly and does not load pytest's conftest.py.
collect_ignore = ["ptf"]


@pytest.fixture(autouse=True)
def isolated_lock_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep switch/runtime lock files out of the shared temp directory.

    §7.19: real runs lock the host-wide temp directory; tests must neither contend with a
    live run nor fail on lock files a previous root-owned run left behind.
    """
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
