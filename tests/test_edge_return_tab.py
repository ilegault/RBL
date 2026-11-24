"""The Edge-Return Test box on the Stepper Motors tab.

The worker is faked (it is hardware-bound); Beamline, MotorTab, the analysis
and the CSV writer are real.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QApplication, QGroupBox

from rbl.config import paths
from rbl.hardware.edge_return import (
    EdgeReturnReading,
    analyse_edge_returns,
    summary_line,
)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _fake_worker(results):
    """Fake EdgeReturnWorker; results[i] is the done() payload of construction i."""
    made = []

    class Fake:
        def __init__(self, galil, axis, speed, cycles, out_counts, parent=None):
            self.axis, self.speed = axis, speed
            self.index = len(made)
            made.append(self)
            self.callbacks = []
            self.progress = MagicMock()
            self.cancelled = False

            class _Sig:
                def connect(_s, fn):
                    self.callbacks.append(fn)
            self.done = _Sig()

        def cancel(self):
            self.cancelled = True

        def isRunning(self):
            return False

        def start(self):
            for fn in self.callbacks:
                fn(*results[self.index])

    return Fake, made


@pytest.fixture
def tab(qapp, monkeypatch, tmp_path):
    from rbl.gui.motor_tab import MotorTab
    from rbl.state.beamline import Beamline

    monkeypatch.setattr(paths, "HOMING_DIR", tmp_path)
    beamline = Beamline()
    beamline.galil = MagicMock(connected=True)
    beamline.set_home_approach_speed(40)
    t = MotorTab(beamline)
    return t


def _console(tab):
    return tab.console.toPlainText()


def _choose_axis(tab, letter):
    label = {"A": "A — X+", "B": "B — X-", "C": "C — Y+", "D": "D — Y-"}[letter]
    tab.cbo_edge_axis.setCurrentIndex(tab.cbo_edge_axis.findText(label))


def _readings(*edges: int) -> list[EdgeReturnReading]:
    return [EdgeReturnReading(e - 10, e) for e in edges]


def test_edge_return_runs_once_at_the_approach_speed(tab, monkeypatch, tmp_path):
    fake, made = _fake_worker([(True, "ok", _readings(1000, 1001, 1000))])
    monkeypatch.setattr("rbl.gui.motor_tab.EdgeReturnWorker", fake)
    _choose_axis(tab, "B")
    tab.btn_edge_run.click()
    assert len(made) == 1
    assert made[0].axis == "B"
    assert made[0].speed == 40
    assert "B [X-] @ 40 cps, stop:" in _console(tab)
    assert "B [X-] @ 40 cps, edge:" in _console(tab)
    files = list(tmp_path.glob("edge_return_B_*.csv"))
    assert len(files) == 1
    assert len(files[0].read_text(encoding="utf-8").splitlines()) - 1 == 3


def test_edge_return_writes_one_csv_for_the_whole_run(tab, monkeypatch, tmp_path):
    fake, _ = _fake_worker([(True, "ok", _readings(1000, 1001, 1000))])
    monkeypatch.setattr("rbl.gui.motor_tab.EdgeReturnWorker", fake)
    _choose_axis(tab, "B")
    tab.btn_edge_run.click()
    files = list(tmp_path.glob("edge_return_B_*.csv"))
    assert len(files) == 1
    assert len(files[0].read_text(encoding="utf-8").splitlines()) - 1 == 3
    assert "# Edge-return CSV:" in _console(tab)


def test_a_failed_speed_ends_the_edge_return_run(tab, monkeypatch):
    msg = "B: home switch did not release within 400 counts — check the sensor"
    fake, made = _fake_worker([(False, msg, _readings(1000, 1001))])
    monkeypatch.setattr("rbl.gui.motor_tab.EdgeReturnWorker", fake)
    _choose_axis(tab, "B")
    tab.btn_edge_run.click()
    assert len(made) == 1
    assert "✗ " + msg in _console(tab)
    assert tab.btn_edge_run.text() == "Run edge-return test"
    assert tab.axes["B"].btn_move.isEnabled()
    assert tab.spn_approach_speed.isEnabled()
    assert tab.btn_auto_home_seq.isEnabled()


def test_edge_return_refuses_while_homing(tab, monkeypatch):
    fake, made = _fake_worker([(True, "ok", _readings(1, 2))])
    monkeypatch.setattr("rbl.gui.motor_tab.EdgeReturnWorker", fake)
    tab.axes["A"].set_external_status("Auto-homing…")
    tab.btn_edge_run.click()
    assert made == []
    assert tab.lbl_edge_note.text() == (
        "Edge-return test needs a connected Galil and no homing in progress.")


def test_edge_return_refuses_without_a_galil(tab, monkeypatch):
    fake, made = _fake_worker([(True, "ok", _readings(1, 2))])
    monkeypatch.setattr("rbl.gui.motor_tab.EdgeReturnWorker", fake)
    tab.galil.connected = False
    tab.btn_edge_run.click()
    assert made == []
    assert "needs a connected Galil" in tab.lbl_edge_note.text()


def test_edge_return_never_defines_zero(tab, monkeypatch):
    fake, _ = _fake_worker([(True, "ok", _readings(1000, 1001, 1000))])
    monkeypatch.setattr("rbl.gui.motor_tab.EdgeReturnWorker", fake)
    tab.btn_edge_run.click()
    assert not tab.galil.define_zero.called
    assert not tab.galil.define_zero_multi.called


def test_edge_return_logs_a_stop_line_and_an_edge_line(tab, monkeypatch):
    readings = [
        EdgeReturnReading(1000, 1020),
        EdgeReturnReading(1001, 1025),
        EdgeReturnReading(1000, 1020),
    ]
    fake, _ = _fake_worker([(True, "ok", readings)])
    monkeypatch.setattr("rbl.gui.motor_tab.EdgeReturnWorker", fake)
    _choose_axis(tab, "B")
    tab.btn_edge_run.click()
    stop_line = summary_line(analyse_edge_returns([1000, 1001, 1000]), "B", 40, "stop")
    edge_line = summary_line(analyse_edge_returns([1020, 1025, 1020]), "B", 40, "edge")
    lines = _console(tab).splitlines()
    stop_idx = next((i for i, line in enumerate(lines) if stop_line in line), None)
    edge_idx = next((i for i, line in enumerate(lines) if edge_line in line), None)
    assert stop_idx is not None
    assert edge_idx is not None
    assert stop_idx < edge_idx
    assert "B [X-] @ 40 cps: " not in _console(tab)


def test_approach_speed_box_sits_in_the_edge_return_box(tab):
    w = tab.spn_approach_speed.parentWidget()
    while w is not None and not isinstance(w, QGroupBox):
        w = w.parentWidget()
    assert w is not None
    assert w.title() == "Edge-Return Test — Homing Repeatability"
    assert "does not affect homing" in tab.spn_approach_speed.toolTip()
    assert tab.lbl_edge_note.text() == (
        "Moves the axis out and runs HM at the approach speed, cycle after cycle, "
        "recording where it stopped and where the home switch releases on the way "
        "back out. Never sets zero. A spread of a few counts is switch jitter; "
        "jumps of about 8 counts are lost steps."
    )
