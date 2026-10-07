"""
tests/test_vacuum_tab.py
Tests for VacuumTab continuous monitoring log integration.
Ticket 21 of logging-and-sessions.
"""
import csv
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from rbl.gui.vacuum_tab import VacuumTab
from rbl.state.beamline import Beamline
from tests.test_vacuum_logger import (
    _FakeChannel,
    _FakeVacuumState,
    _FakeXgsReading,
)

CHICAGO = ZoneInfo("America/Chicago")


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def make_state(
    unix_t: float = 100.0,
    gauge_label: str = "P1",
    pressure: float = 1e-6,
) -> _FakeVacuumState:
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


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as f:
        filtered = (line for line in f if not line.startswith("#"))
        return list(csv.DictReader(filtered))


def test_vacuum_tab_creates_one_csv_under_month_folder(qapp, tmp_path):
    beamline = Beamline()
    tab = VacuumTab(beamline, monitor_log_root=tmp_path)
    btn_log = tab.findChild(QPushButton, "vacuum_log_btn")
    lbl_path = tab.findChild(QLabel, "vacuum_log_path")

    # Deliver state through the tab's real vacuum_changed connection
    st = make_state(100.0)
    beamline.vacuum_changed.emit(st)

    # Button reads "Stop Logging"
    assert btn_log.text() == "Stop Logging"

    # CSV created under <root>/YYYY-MM/
    now_local = datetime.now().astimezone()
    month_dir = tmp_path / now_local.strftime("%Y-%m")
    assert month_dir.is_dir()
    csvs = list(month_dir.glob("*.csv"))
    assert len(csvs) == 1

    # Path label shows the CSV path
    assert lbl_path.text() == str(csvs[0])


def test_stop_logging_preserves_row_count_and_does_not_auto_restart(qapp, tmp_path):
    beamline = Beamline()
    tab = VacuumTab(beamline, monitor_log_root=tmp_path)
    btn_log = tab.findChild(QPushButton, "vacuum_log_btn")
    lbl_path = tab.findChild(QLabel, "vacuum_log_path")

    # Deliver 3 states
    for i in range(3):
        beamline.vacuum_changed.emit(make_state(100.0 + i))

    now_local = datetime.now().astimezone()
    month_dir = tmp_path / now_local.strftime("%Y-%m")
    csvs = list(month_dir.glob("*.csv"))
    assert len(csvs) == 1
    csv_file = csvs[0]

    rows_before = len(read_csv_rows(csv_file))
    assert rows_before == 3

    # Click the button to stop logging
    btn_log.click()

    assert btn_log.text() == "Start Logging"
    assert lbl_path.text().startswith(f"Saved: {csv_file}")

    # Deliver 5 more states
    for i in range(5):
        beamline.vacuum_changed.emit(make_state(200.0 + i))

    # Row count unchanged and no other file created
    rows_after = len(read_csv_rows(csv_file))
    assert rows_after == 3
    assert len(list(month_dir.glob("*.csv"))) == 1
    assert btn_log.text() == "Start Logging"
    assert lbl_path.text().startswith("Saved:")


def test_start_logging_again_creates_second_file_and_shows_path(qapp, tmp_path):
    beamline = Beamline()
    tab = VacuumTab(beamline, monitor_log_root=tmp_path)
    btn_log = tab.findChild(QPushButton, "vacuum_log_btn")
    lbl_path = tab.findChild(QLabel, "vacuum_log_path")

    # Deliver 1 state, then stop
    beamline.vacuum_changed.emit(make_state(100.0))
    btn_log.click()
    assert btn_log.text() == "Start Logging"

    # Click again to start logging
    btn_log.click()
    assert btn_log.text() == "Stop Logging"

    # Deliver one more state
    beamline.vacuum_changed.emit(make_state(200.0))

    now_local = datetime.now().astimezone()
    month_dir = tmp_path / now_local.strftime("%Y-%m")
    csvs = sorted(list(month_dir.glob("*.csv")))
    assert len(csvs) == 2

    assert lbl_path.text() == str(csvs[1])


def test_no_start_logging_call_from_on_vacuum_state():
    """Verify that _on_vacuum_state does not call _start_logging()."""
    source = Path("src/rbl/gui/vacuum_tab.py").read_text(encoding="utf-8")
    method_match = re.search(
        r"def _on_vacuum_state\(self, state\):.*?(?=\n    def )",
        source,
        re.DOTALL,
    )
    assert method_match is not None
    assert not re.search(r"_start_logging\s*\(", method_match.group(0)), (
        "_on_vacuum_state must not call _start_logging()"
    )
