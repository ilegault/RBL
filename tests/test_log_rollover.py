"""Tests for services/log_rollover.py: pure time/name logic, no clock read."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from rbl.services.log_rollover import (
    crossed_local_midnight,
    month_folder,
    timestamped_stem,
    unused_path,
)

UTC = timezone.utc
CHICAGO = ZoneInfo("America/Chicago")


def test_midnight_crossed():
    a = datetime(2026, 9, 29, 23, 59, 59, tzinfo=UTC)
    b = datetime(2026, 9, 30, 0, 0, 0, tzinfo=UTC)
    assert crossed_local_midnight(a, b) is True


def test_same_day_not_crossed():
    a = datetime(2026, 9, 29, 0, 0, 0, tzinfo=UTC)
    b = datetime(2026, 9, 29, 23, 59, 59, tzinfo=UTC)
    assert crossed_local_midnight(a, b) is False


def test_month_end_folders():
    root = Path("root")
    assert month_folder(root, datetime(2026, 10, 31, 23, 59, tzinfo=UTC)) == root / "2026-10"
    assert month_folder(root, datetime(2026, 11, 1, 0, 0, tzinfo=UTC)) == root / "2026-11"


def test_year_end_folders():
    root = Path("root")
    assert month_folder(root, datetime(2026, 12, 31, 23, 59, tzinfo=UTC)) == root / "2026-12"
    assert month_folder(root, datetime(2027, 1, 1, 0, 0, tzinfo=UTC)) == root / "2027-01"


def test_dst_fall_back_is_same_local_day():
    first = datetime(2026, 11, 1, 1, 30, tzinfo=CHICAGO, fold=0)   # CDT
    second = datetime(2026, 11, 1, 1, 30, tzinfo=CHICAGO, fold=1)  # CST
    assert first.utcoffset() != second.utcoffset()
    assert crossed_local_midnight(first, second) is False


def test_unused_path_counts_up(tmp_path):
    assert unused_path(tmp_path, "run", ".csv") == tmp_path / "run.csv"
    (tmp_path / "run.csv").write_text("a")
    assert unused_path(tmp_path, "run", ".csv") == tmp_path / "run_2.csv"
    (tmp_path / "run_2.csv").write_text("b")
    assert unused_path(tmp_path, "run", ".csv") == tmp_path / "run_3.csv"


def test_timestamped_stem():
    dt = datetime(2026, 9, 29, 14, 30, 12, tzinfo=timezone(timedelta(hours=-5)))
    assert timestamped_stem("vacuum", dt) == "vacuum_20260929T143012"
