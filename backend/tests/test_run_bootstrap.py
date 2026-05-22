"""Run directory + journal bootstrap."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from engine.persistence.run_bootstrap import resolve_run_dir  # noqa: E402


def test_resolve_run_dir_when_journal_only(tmp_path: Path) -> None:
    settings = MagicMock()
    settings.persist_enabled = False
    settings.log_file_enabled = False
    settings.journal_enabled = True
    settings.persist_dir = str(tmp_path)

    run_dir = resolve_run_dir(settings, tmp_path)
    assert run_dir is not None
    assert run_dir.is_dir()


def test_resolve_run_dir_none_when_all_disabled(tmp_path: Path) -> None:
    settings = MagicMock()
    settings.persist_enabled = False
    settings.log_file_enabled = False
    settings.journal_enabled = False
    settings.persist_dir = str(tmp_path)

    assert resolve_run_dir(settings, tmp_path) is None
