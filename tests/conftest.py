"""Shared pytest isolation for host-wide lock files."""

import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_lock_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep switch/runtime lock files out of the shared temp directory.

    §7.19: real runs lock the host-wide temp directory; tests must neither contend with a
    live run nor fail on lock files a previous root-owned run left behind.
    """
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
