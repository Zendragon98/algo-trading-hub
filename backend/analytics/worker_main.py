"""Analytics worker — runs heavy jobs off the trading process event loop.

Started by ``main.py`` (embedded) or manually:

    python -m analytics.worker_main
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time

from analytics.jobs import claim_next_job, execute_job, finish_job, poll_interval_sec, resolve_jobs_dir

logger = logging.getLogger(__name__)
_stop = False


def _handle_signal(_signum: int, _frame: object) -> None:
    global _stop
    _stop = True


def run_loop(jobs_dir, *, once: bool = False) -> None:
    root = resolve_jobs_dir(jobs_dir)
    logger.info("analytics worker started (jobs_dir=%s)", root)
    while not _stop:
        claimed = claim_next_job(root)
        if claimed is None:
            if once:
                break
            time.sleep(poll_interval_sec())
            continue
        record, running_path = claimed
        logger.info("running job %s type=%s", record.id, record.type)
        try:
            result = execute_job(record)
            finish_job(root, record, running_path=running_path, result=result)
            logger.info("job %s completed", record.id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("job %s failed", record.id)
            finish_job(root, record, running_path=running_path, error=str(exc))
        if once:
            break


def main() -> None:
    parser = argparse.ArgumentParser(description="Analytics job worker")
    parser.add_argument(
        "--jobs-dir",
        default=None,
        help="Job queue root (default: backend/data/jobs)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process at most one pending job then exit",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [analytics-worker] %(message)s",
    )
    signal.signal(signal.SIGINT, _handle_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_signal)
    try:
        run_loop(args.jobs_dir, once=args.once)
    except KeyboardInterrupt:
        logger.info("analytics worker interrupted")
    logger.info("analytics worker stopped")


if __name__ == "__main__":
    main()
    sys.exit(0)
