"""The Edge-Return Test box on the Stepper Motors tab.

The worker is faked (it is hardware-bound); Beamline, MotorTab, the analysis
and the CSV writer are real.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QApplication

from rbl.config import paths


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
    beamline.set_home_pass_speeds((300, 120, 40))
    t = MotorTab(beamline)
    return t


def _console(tab):
    return tab.console.toPlainText()


def _choose_axis(tab, letter):
    label = {"A": "A — X+", "B": "B — X-", "C": "C — Y+", "D": "D — Y-"}[letter]
    tab.cbo_edge_axis.setCurrentIndex(tab.cbo_edge_axis.findText(label))


def test_edge_return_runs_every_pass_speed_in_order(tab, monkeypatch):
    fake, made = _fake_worker([(True, "ok", [1000, 1001, 1000])] * 3)
    monkeypatch.setattr("rbl.gui.motor_tab.EdgeReturnWorker", fake)
    _choose_axis(tab, "B")
    tab.btn_edge_run.click()
    assert [m.speed for m in made] == [300, 120, 40]
    assert {m.axis for m in made} == {"B"}
    assert "B [X-] @ 300 cps:" in _console(tab)
    assert "B [X-] @ 40 cps:" in _console(tab)


def test_edge_return_writes_one_csv_for_the_whole_run(tab, monkeypatch, tmp_path):
    fake, _ = _fake_worker([(True, "ok", [1000, 1001, 1000])] * 3)
    monkeypatch.setattr("rbl.gui.motor_tab.EdgeReturnWorker", fake)
    _choose_axis(tab, "B")
    tab.btn_edge_run.click()
    files = list(tmp_path.glob("edge_return_B_*.csv"))
    assert len(files) == 1
    assert len(files[0].read_text(encoding="utf-8").splitlines()) - 1 == 9
    assert "# Edge-return CSV:" in _console(tab)


def test_a_failed_speed_ends_the_edge_return_run(tab, monkeypatch):
    msg = "B: cycle 2/10 did not stop on the home switch (stop code 3)"
    fake, made = _fake_worker([(True, "ok", [1000, 1001]), (False, msg, [1000])])
    monkeypatch.setattr("rbl.gui.motor_tab.EdgeReturnWorker", fake)
    _choose_axis(tab, "B")
    tab.btn_edge_run.click()
    assert len(made) == 2
    assert "✗ " + msg in _console(tab)
    assert tab.btn_edge_run.text() == "Run edge-return test"
    assert tab.axes["B"].btn_move.isEnabled()
    assert tab.spn_pass1.isEnabled()
    assert tab.btn_auto_home_seq.isEnabled()


def test_edge_return_refuses_while_homing(tab, monkeypatch):
    fake, made = _fake_worker([(True, "ok", [1, 2])] * 3)
    monkeypatch.setattr("rbl.gui.motor_tab.EdgeReturnWorker", fake)
    tab.axes["A"].set_external_status("Auto-homing…")
    tab.btn_edge_run.click()
    assert made == []
    assert tab.lbl_edge_note.text() == (
        "Edge-return test needs a connected Galil and no homing in progress.")


def test_edge_return_refuses_without_a_galil(tab, monkeypatch):
    fake, made = _fake_worker([(True, "ok", [1, 2])] * 3)
    monkeypatch.setattr("rbl.gui.motor_tab.EdgeReturnWorker", fake)
    tab.galil.connected = False
    tab.btn_edge_run.click()
    assert made == []
    assert "needs a connected Galil" in tab.lbl_edge_note.text()


def test_edge_return_never_defines_zero(tab, monkeypatch):
    fake, _ = _fake_worker([(True, "ok", [1000, 1001, 1000])] * 3)
    monkeypatch.setattr("rbl.gui.motor_tab.EdgeReturnWorker", fake)
    tab.btn_edge_run.click()
    assert not tab.galil.define_zero.called
    assert not tab.galil.define_zero_multi.called
