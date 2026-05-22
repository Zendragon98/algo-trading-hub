"""Spawn and supervise the analytics worker subprocess."""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from common.config import Settings

logger = logging.getLogger(__name__)


class AnalyticsWorkerSupervisor:
    def __init__(self) -> None:
        self._proc: subprocess.Popen[bytes] | None = None

    def start(self, settings: Settings, *, jobs_dir: Path) -> None:
        if not settings.analytics_worker_enabled:
            logger.info("analytics worker disabled")
            return
        mode = (settings.analytics_worker_mode or "embedded").strip().lower()
        if mode == "disabled":
            logger.info("analytics worker mode=disabled")
            return
        if mode == "external":
            logger.info(
                "analytics worker mode=external — start manually: "
                "python -m analytics.worker_main --jobs-dir %s",
                jobs_dir,
            )
            return
        if self._proc is not None and self._proc.poll() is None:
            return
        cmd = [
            sys.executable,
            "-m",
            "analytics.worker_main",
            "--jobs-dir",
            str(jobs_dir),
        ]
        self._proc = subprocess.Popen(
            cmd,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        logger.info("analytics worker started pid=%s", self._proc.pid)

    def stop(self, *, timeout: float = 10.0) -> None:
        proc = self._proc
        if proc is None:
            return
        if proc.poll() is not None:
            self._proc = None
            return
        logger.info("stopping analytics worker pid=%s", proc.pid)
        proc.terminate()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            logger.warning("analytics worker did not exit; killing")
            proc.kill()
            proc.wait(timeout=5.0)
        self._proc = None

    @property
    def pid(self) -> int | None:
        if self._proc is None:
            return None
        return self._proc.pid
