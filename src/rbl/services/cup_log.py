"""
cup_log.py
The single owner of whether a Faraday cup log is open.

WHY THIS EXISTS
---------------
Before ADR 0003 amendment C1, ``FaradayCupTab`` built its own ``CupSessionWriter``
in its constructor, so a cup log existed whether or not anyone had started a
session, and nothing else could tell whether it was open or where. Logging is now
an explicit state: a cup log is open because a session opened it, or because the
operator started a test log from the Overview panel. This class holds that state
so the tab, the Overview cup panel and the session code all ask one object.

Rules it enforces:
- There is never more than one cup log. Opening while one is open closes it first
  and emits ``closed`` before ``opened``, so listeners see a clean sequence.
- A session log is ``<session folder>/cup.csv`` (``session_id="cup"``). A test log
  is dated under the cup root and never reuses a name (``unused_path``).
- Reading totals back from earlier logs (``read_dose_totals``,
  ``find_previous_session_cup_log``) is pure and lives in ``cup_log_totals`` so it
  stays provably Qt-free; it is re-exported here for callers.

The small frozen dataclasses (``RestartChoice``, ``ContinueChoice``, ``CupView``)
are the values later tickets pass between the stop/restart dialogs, the tab and the
Overview panel. They carry data only.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal

from rbl.config.paths import FARADAY_CUP_DIR
from rbl.services.cup_log_totals import find_previous_session_cup_log, read_dose_totals
from rbl.services.cup_session_writer import CupSessionWriter
from rbl.services.log_rollover import month_folder, timestamped_stem, unused_path

__all__ = [
    "ContinueChoice",
    "CupLog",
    "CupLogKind",
    "CupView",
    "RestartChoice",
    "find_previous_session_cup_log",
    "read_dose_totals",
]


class CupLogKind(Enum):
    SESSION = "session"
    TEST = "test"


@dataclass(frozen=True)
class RestartChoice:
    """Operator's answer when restarting automatic insertion after a stop."""
    resume_schedule: bool
    beam_on_during_gap: bool


@dataclass(frozen=True)
class ContinueChoice:
    """Operator's answer when a new session could continue an earlier dose."""
    continue_dose: bool
    beam_on_during_gap: bool


@dataclass(frozen=True)
class CupView:
    """What the Overview cup panel renders; ``None`` fields mean not available."""
    logging: bool
    log_path: str | None
    run_open: bool
    automatic_running: bool
    charge_c: float
    fluence: float | None
    dpa: float | None


class CupLog(QObject):
    """Owns the one open cup log; emits ``opened(path, kind)`` and ``closed(path)``."""

    opened = Signal(str, str)
    closed = Signal(str)

    def __init__(self, test_root: Path | None = None) -> None:
        super().__init__()
        self._test_root: Path = Path(test_root) if test_root is not None else FARADAY_CUP_DIR
        self._writer: CupSessionWriter | None = None
        self._kind: CupLogKind | None = None
        self._settings_provider: Callable[[], dict[str, Any]] | None = None

    def set_writer_settings_provider(
        self, provider: Callable[[], dict[str, Any]] | None
    ) -> None:
        """Register who knows the acquisition settings in force at open time.

        The thresholds and settle window go into the CSV header, so they must be the
        ones the detector is using when the file opens, not compiled-in defaults.
        The Faraday Cup tab registers itself; with none registered the writer's own
        defaults apply.
        """
        self._settings_provider = provider

    def _writer_settings(self) -> dict[str, Any]:
        return dict(self._settings_provider()) if self._settings_provider is not None else {}

    def open_for_session(
        self, folder: Path, continuation: dict[str, Any] | None = None
    ) -> CupSessionWriter:
        """Open ``folder/cup.csv`` for a session, closing any open cup log first."""
        self.close()
        writer = CupSessionWriter(
            session_id="cup",
            output_dir=Path(folder),
            continuation=continuation,
            **self._writer_settings(),
        )
        return self._adopt(writer, CupLogKind.SESSION)

    def open_test(self, now_local: datetime) -> CupSessionWriter:
        """Open a dated test log under the cup root, closing any open cup log first."""
        self.close()
        folder = month_folder(self._test_root, now_local)
        folder.mkdir(parents=True, exist_ok=True)
        path = unused_path(folder, timestamped_stem("cup", now_local), ".csv")
        writer = CupSessionWriter(
            session_id=path.stem, output_dir=folder, **self._writer_settings()
        )
        return self._adopt(writer, CupLogKind.TEST)

    def close(self) -> str | None:
        """Close the open cup log and return its path; ``None`` if none was open."""
        writer = self._writer
        if writer is None:
            return None
        path = writer.close()
        self._writer = None
        self._kind = None
        self.closed.emit(path)
        return path

    def _adopt(self, writer: CupSessionWriter, kind: CupLogKind) -> CupSessionWriter:
        self._writer = writer
        self._kind = kind
        self.opened.emit(writer.csv_path, kind.value)
        return writer

    @property
    def writer(self) -> CupSessionWriter | None:
        return self._writer

    @property
    def kind(self) -> CupLogKind | None:
        return self._kind

    @property
    def path(self) -> str | None:
        return self._writer.csv_path if self._writer is not None else None
