"""
log_rollover.py
Pure helpers every log uses to pick a month folder, a file name, to notice
local midnight, and to find a file name that is not taken.

WHY THIS EXISTS
---------------
A vacuum log left running for two weeks grew to one 176 MB file, and a second
run that reused a name overwrote the first. Rolling over by local day and
never reusing a name fixes both, but only if midnight, month-end, year-end and
the DST fall-back hour are right. So these functions take the time as an
argument and never read a clock: tests pass times instead of waiting for them.
Callers supply tz-aware local datetimes (``datetime.now().astimezone()``).

`unused_path` is the only function that touches the filesystem, and only to
test existence. Every new log file name goes through it.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path


def month_folder(root: Path, local_dt: datetime) -> Path:
    """``root / "YYYY-MM"`` for the local month of ``local_dt``."""
    return Path(root) / f"{local_dt.year:04d}-{local_dt.month:02d}"


def timestamped_stem(prefix: str, local_dt: datetime) -> str:
    """``prefix_YYYYMMDDTHHMMSS`` from the wall-clock fields of ``local_dt``."""
    return f"{prefix}_{local_dt:%Y%m%dT%H%M%S}"


def crossed_local_midnight(opened_local: datetime, now_local: datetime) -> bool:
    """True when the local calendar date differs.

    Compares dates, not elapsed time, so the 25-hour DST fall-back day does not
    roll early and the 23-hour spring-forward day does not roll late.
    """
    return now_local.date() != opened_local.date()


def unused_path(folder: Path, stem: str, suffix: str) -> Path:
    """First of ``stem``, ``stem_2``, ``stem_3``... plus ``suffix`` not on disk."""
    folder = Path(folder)
    candidate = folder / f"{stem}{suffix}"
    n = 2
    while candidate.exists():
        candidate = folder / f"{stem}_{n}{suffix}"
        n += 1
    return candidate
