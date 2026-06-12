from __future__ import annotations

from argparse import Namespace

from common.config import Settings
from main import _should_autostart_engine


def test_no_engine_overrides_env_autostart() -> None:
    settings = Settings(engine_autostart=True)
    args = Namespace(engine=False, no_engine=True)

    assert not _should_autostart_engine(args, settings)


def test_engine_flag_overrides_stopped_default() -> None:
    settings = Settings(engine_autostart=False)
    args = Namespace(engine=True, no_engine=False)

    assert _should_autostart_engine(args, settings)


def test_default_boot_respects_engine_autostart_setting() -> None:
    args = Namespace(engine=False, no_engine=False)

    assert not _should_autostart_engine(args, Settings(engine_autostart=False))
    assert _should_autostart_engine(args, Settings(engine_autostart=True))
