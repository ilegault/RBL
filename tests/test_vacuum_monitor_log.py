"""
tests/test_vacuum_monitor_log.py
Tests for continuous vacuum monitoring log rollover at local midnight.
Ticket 20 of logging-and-sessions.
"""
import csv
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from rbl.services.vacuum_logger import gauge_labels
from rbl.services.vacuum_monitor_log import VacuumMonitorLog
from tests.test_vacuum_logger import (
    _FakeChannel,
    _FakeVacuumState,
    _FakeXgsReading,
)

CHICAGO = ZoneInfo("America/Chicago")


def _make_state(unix_t: float, gauge_label: str = "P1", pressure: float = 1e-6) -> _FakeVacuumState:
    return _FakeVacuumState(
        timestamp=unix_t,
        xgs_readings=[
            _FakeXgsReading(
                channel=_FakeChannel(label=gauge_label),
                pressure=pressure,
                raw=str(pressure),
                state="OK",
            )
        ],
    )


def _read_csv(path: str) -> tuple[list[str], list[dict[str, str]]]:
    """Return raw comment lines and data rows as dicts."""
    comments = []
    rows = []
    with open(path, encoding="utf-8") as f:
        # Read comments
        pos = f.tell()
        line = f.readline()
        while line.startswith("#"):
            comments.append(line.strip())
            pos = f.tell()
            line = f.readline()
        f.seek(pos)
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(r)
    return comments, rows


def test_gauge_labels_function_and_vacuum_tab():
    st = _make_state(100.0, "CH1")
    assert gauge_labels(st) == ["xgs600:CH1"]

    from rbl.gui.vacuum_tab import VacuumTab
    assert VacuumTab._build_gauge_labels(st) == ["xgs600:CH1"]


def test_midnight_rollover_two_files_and_headers(tmp_path):
    log = VacuumMonitorLog(root=tmp_path)

    # 10 states at 23:59:50-23:59:59 on 2026-05-15
    for sec in range(50, 60):
        dt = datetime(2026, 5, 15, 23, 59, sec, tzinfo=CHICAGO)
        unix_t = dt.timestamp()
        log.write(_make_state(unix_t), dt)

    # 10 states at 00:00:00-00:00:09 on 2026-05-16
    for sec in range(10):
        dt = datetime(2026, 5, 16, 0, 0, sec, tzinfo=CHICAGO)
        unix_t = dt.timestamp()
        log.write(_make_state(unix_t), dt)

    log.stop()

    month_dir = tmp_path / "2026-05"
    assert month_dir.is_dir()
    csvs = sorted(list(month_dir.glob("*.csv")))
    assert len(csvs) == 2

    c1, rows1 = _read_csv(str(csvs[0]))
    c2, rows2 = _read_csv(str(csvs[1]))

    assert len(rows1) == 10
    assert len(rows2) == 10
    assert len(rows1) + len(rows2) == 20

    # Header comments exist
    assert any(c.startswith("#") for c in c1)
    assert any(c.startswith("#") for c in c2)

    # Second file's first row has unix_time of 00:00:00 state
    t0 = datetime(2026, 5, 16, 0, 0, 0, tzinfo=CHICAGO).timestamp()
    assert float(rows2[0]["unix_time"]) == pytest.approx(t0, abs=1e-3)


def test_month_end_crossing_creates_new_month_folder(tmp_path):
    log = VacuumMonitorLog(root=tmp_path)

    # Pre-midnight: 2026-05-31 23:59:59
    dt1 = datetime(2026, 5, 31, 23, 59, 59, tzinfo=CHICAGO)
    log.write(_make_state(dt1.timestamp()), dt1)

    # Post-midnight: 2026-06-01 00:00:01
    dt2 = datetime(2026, 6, 1, 0, 0, 1, tzinfo=CHICAGO)
    log.write(_make_state(dt2.timestamp()), dt2)
    log.stop()

    may_dir = tmp_path / "2026-05"
    june_dir = tmp_path / "2026-06"
    assert may_dir.is_dir()
    assert june_dir.is_dir()

    may_csvs = list(may_dir.glob("*.csv"))
    june_csvs = list(june_dir.glob("*.csv"))
    assert len(may_csvs) == 1
    assert len(june_csvs) == 1

    _, rows_may = _read_csv(str(may_csvs[0]))
    _, rows_june = _read_csv(str(june_csvs[0]))
    assert len(rows_may) == 1
    assert len(rows_june) == 1


def test_stop_prevents_further_writes_and_returns_path(tmp_path):
    log = VacuumMonitorLog(root=tmp_path)
    dt1 = datetime(2026, 5, 15, 12, 0, 0, tzinfo=CHICAGO)
    log.write(_make_state(dt1.timestamp()), dt1)

    closed_path = log.stop()
    assert closed_path is not None
    assert Path(closed_path).exists()
    assert not log.is_running

    # 5 more writes including after midnight
    for sec in range(4):
        dt = datetime(2026, 5, 15, 12, 0, 1 + sec, tzinfo=CHICAGO)
        log.write(_make_state(dt.timestamp()), dt)
    dt_next_day = datetime(2026, 5, 16, 1, 0, 0, tzinfo=CHICAGO)
    log.write(_make_state(dt_next_day.timestamp()), dt_next_day)

    # Assert no new files and closed file untouched (still 1 data row)
    month_dir = tmp_path / "2026-05"
    assert len(list(month_dir.glob("*.csv"))) == 1
    assert not (tmp_path / "2026-05" / "2026-05-16").exists()

    _, rows = _read_csv(closed_path)
    assert len(rows) == 1


def test_stop_then_start_opens_new_file(tmp_path):
    log = VacuumMonitorLog(root=tmp_path)
    dt1 = datetime(2026, 5, 15, 10, 0, 0, tzinfo=CHICAGO)
    log.write(_make_state(dt1.timestamp()), dt1)
    p1 = log.stop()

    log.start()
    assert log.is_running
    dt2 = datetime(2026, 5, 15, 11, 0, 0, tzinfo=CHICAGO)
    log.write(_make_state(dt2.timestamp()), dt2)
    p2 = log.stop()

    assert p1 != p2
    assert "20260515T100000" in p1
    assert "20260515T110000" in p2


def test_gauge_set_change_reopens_with_suffix(tmp_path):
    log = VacuumMonitorLog(root=tmp_path)
    dt = datetime(2026, 5, 15, 14, 0, 0, tzinfo=CHICAGO)

    st1 = _make_state(dt.timestamp(), "P1")
    log.write(st1, dt)

    st2 = _make_state(dt.timestamp() + 1, "P2")  # Different gauge label
    log.write(st2, dt)
    log.stop()

    month_dir = tmp_path / "2026-05"
    csvs = sorted(list(month_dir.glob("*.csv")))
    assert len(csvs) == 2
    assert "_2.csv" in csvs[1].name

    c1, r1 = _read_csv(str(csvs[0]))
    c2, r2 = _read_csv(str(csvs[1]))
    assert "xgs600:P1" in r1[0]
    assert "xgs600:P2" in r2[0]
