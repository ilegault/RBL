"""
vacuum_monitor_log.py
Continuous vacuum monitoring log rolling over at local midnight.

WHY THIS EXISTS
---------------
ADR 0004 (Decisions 1, 2, 3, 8) establishes that the vacuum monitoring log is the only
continuous log in RBL. It runs without an operator explicitly opening a session, rolling
over daily at local midnight (America/Chicago) into month folders (YYYY-MM).

This module encapsulates that rollover behavior as a pure, testable service:
- Takes local timestamps as parameters rather than reading the system clock.
- Groups files into `root / "YYYY-MM" / vacuum_YYYYMMDDTHHMMSS.csv`.
- Never overwrites files (via log_rollover.unused_path).
- Rolls to a new file at local midnight, sending the post-midnight row to the new file only.
- Closes and reopens when the connected gauge set changes mid-day.
- Stops cleanly on `stop()` and remains stopped until `start()`.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from rbl.config.paths import VACUUM_DIR
from rbl.services.log_rollover import (
    crossed_local_midnight,
    month_folder,
    timestamped_stem,
)
from rbl.services.vacuum_logger import VacuumLogger, gauge_labels

log = logging.getLogger(__name__)


def _default_comment_lines(state) -> list[str]:
    lines = [f"vacuum_logger RBL {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}"]
    if getattr(state, "xgs_connected", False):
        lines.append(f"xgs600: units={getattr(state, 'units_xgs', '')}")
    if getattr(state, "vgc_connected", False):
        lines.append(f"vgc083: units={getattr(state, 'units_vgc', '')}")
    return lines


class VacuumMonitorLog:
    """Continuous vacuum monitoring log manager with daily rollover at local midnight."""

    def __init__(
        self,
        root: Path | None = None,
        *,
        comment_lines_fn: Callable[[object], list[str]] | None = None,
    ) -> None:
        self._root = Path(root) if root is not None else VACUUM_DIR
        self._comment_lines_fn = comment_lines_fn
        self._is_running: bool = True
        self._current_logger: VacuumLogger | None = None
        self._opened_local: datetime | None = None

    @property
    def is_running(self) -> bool:
        """True when the log is actively ingesting states; False when stopped."""
        return self._is_running

    @property
    def current_path(self) -> str | None:
        """Filesystem path to the currently open CSV log file, or None if not open."""
        return self._current_logger.csv_path if self._current_logger else None

    def start(self) -> None:
        """Resume or start logging on incoming states."""
        self._is_running = True

    def stop(self) -> str | None:
        """Stop logging and close the current file. Returns the path of the closed file or None."""
        self._is_running = False
        return self._close_current()

    def _open_new(self, state: object, now_local: datetime) -> VacuumLogger:
        folder = month_folder(self._root, now_local)
        stem = timestamped_stem("vacuum", now_local)
        labels = gauge_labels(state)
        logger = VacuumLogger(labels, output_dir=folder, file_stem=stem)
        if self._comment_lines_fn:
            comment_lines = self._comment_lines_fn(state)
        else:
            comment_lines = _default_comment_lines(state)
        logger.write_header_comment(comment_lines)
        self._current_logger = logger
        self._opened_local = now_local
        return logger

    def _close_current(self) -> str | None:
        if self._current_logger is not None:
            path = self._current_logger.close()
            self._current_logger = None
            self._opened_local = None
            return path
        return None

    def write(self, state: object, now_local: datetime) -> None:
        """Ingest one VacuumState snapshot at given local time according to rollover rules."""
        if not self._is_running:
            return

        # Rule 1: No file open -> open at month_folder, write header, write row
        if self._current_logger is None:
            logger = self._open_new(state, now_local)
            logger.write_row(state)
            return

        # Rule 2: Crossed local midnight -> close old file, open new file, write header, write row
        if self._opened_local is not None and crossed_local_midnight(self._opened_local, now_local):
            self._close_current()
            logger = self._open_new(state, now_local)
            logger.write_row(state)
            return

        # Rule 3: Gauge set changed -> write_row returns False -> close and reopen
        ok = self._current_logger.write_row(state)
        if not ok:
            self._close_current()
            logger = self._open_new(state, now_local)
            logger.write_row(state)
