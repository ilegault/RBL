"""The Switch Map box on the Stepper Motors tab.

The worker is faked (it is hardware-bound); Beamline, MotorTab, SwitchMapRow
and write_switch_map_csv are real.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QApplication

from rbl.config import paths
from rbl.hardware.switch_map import SwitchMapRow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _fake_worker(
    *,
    progress_msgs=(),
    rows_to_emit=(),
    done_payload=(True, "ok", []),
    never_finish=False,
):
    """Fake SwitchMapWorker whose start() emits configured signals."""
    made = []

    class Fake:
        def __init__(self, galil, axis, parent=None):
            self.galil = galil
            self.axis = axis
            self.index = len(made)
            made.append(self)
            self._progress_cbs = []
            self._row_cbs = []
            self._done_cbs = []
            self.cancelled = False

            class _Sig:
                def __init__(self, cbs):
                    self._cbs = cbs

                def connect(self, fn):
                    self._cbs.append(fn)

            self.progress = _Sig(self._progress_cbs)
            self.row = _Sig(self._row_cbs)
            self.done = _Sig(self._done_cbs)

        def cancel(self):
            self.cancelled = True

        def isRunning(self):
            return never_finish

        def start(self):
            for msg in progress_msgs:
                for fn in self._progress_cbs:
                    fn(msg)
            for row in rows_to_emit:
                for fn in self._row_cbs:
                    fn(row)
            if not never_finish:
                for fn in self._done_cbs:
                    fn(*done_payload)

    return Fake, made


@pytest.fixture
def tab(qapp, monkeypatch, tmp_path):
    from rbl.gui.motor_tab import MotorTab
    from rbl.state.beamline import Beamline

    monkeypatch.setattr(paths, "HOMING_DIR", tmp_path)
    beamline = Beamline()
    beamline.galil = MagicMock(connected=True)
    return MotorTab(beamline)


def _console(tab):
    return tab.console.toPlainText()


def _choose_axis(tab, letter):
    label = {"A": "A — X+", "B": "B — X-", "C": "C — Y+", "D": "D — Y-"}[letter]
    tab.cbo_switch_axis.setCurrentIndex(tab.cbo_switch_axis.findText(label))


def test_switch_map_box_shows_each_step_live(tab, monkeypatch):
    row = SwitchMapRow(3, "in", -3, True, True, False)
    fake, made = _fake_worker(rows_to_emit=[row], never_finish=True)
    monkeypatch.setattr("rbl.gui.motor_tab.SwitchMapWorker", fake)
    _choose_axis(tab, "B")
    tab.btn_switch_run.click()
    assert len(made) == 1
    assert made[0].axis == "B"
    assert tab.lbl_switch_live.text() == "step 3 — -3 cts — F○ R● H●"


def test_switch_map_writes_its_csv_and_logs_it(tab, monkeypatch, tmp_path):
    rows = [
        SwitchMapRow(1, "in", -2, False, False, False),
        SwitchMapRow(2, "in", -3, True, True, False),
    ]
    summary = (
        "B: in — home trips at -3, reverse trips at -3 (gap 0); "
        "out — home releases at never, reverse releases at never"
    )
    fake, _ = _fake_worker(
        progress_msgs=["B: in — home trips at -3 cts"],
        done_payload=(True, summary, rows),
    )
    monkeypatch.setattr("rbl.gui.motor_tab.SwitchMapWorker", fake)
    _choose_axis(tab, "B")
    tab.btn_switch_run.click()
    files = list(tmp_path.glob("switch_map_B_*.csv"))
    assert len(files) == 1
    assert "B: in — home trips at -3 cts" in _console(tab)
    assert "# Switch map CSV:" in _console(tab)


def test_switch_map_refuses_while_homing(tab, monkeypatch):
    fake, made = _fake_worker()
    monkeypatch.setattr("rbl.gui.motor_tab.SwitchMapWorker", fake)
    tab.axes["A"].set_external_status("Auto-homing…")
    tab.btn_switch_run.click()
    assert made == []
    assert tab.lbl_switch_note.text() == (
        "Switch map needs a connected Galil and no homing or edge-return test in progress."
    )


def test_homing_refuses_while_a_switch_map_runs(tab, monkeypatch):
    fake, _ = _fake_worker(never_finish=True)
    auto_made = []
    edge_made = []
    home_made = []

    class FakeAuto:
        def __init__(self, *args, **kwargs):
            auto_made.append(self)

    class FakeEdge:
        def __init__(self, *args, **kwargs):
            edge_made.append(self)

    class FakeHome:
        def __init__(self, *args, **kwargs):
            home_made.append(self)

    monkeypatch.setattr("rbl.gui.motor_tab.SwitchMapWorker", fake)
    monkeypatch.setattr("rbl.gui.motor_tab.AutoHomeAllWorker", FakeAuto)
    monkeypatch.setattr("rbl.gui.motor_tab.EdgeReturnWorker", FakeEdge)
    monkeypatch.setattr("rbl.gui.motor_tab.HomingWorker", FakeHome)
    monkeypatch.setattr(tab, "_confirm_auto_home", lambda *a: True)

    _choose_axis(tab, "B")
    tab.btn_switch_run.click()

    assert not tab.spn_approach_speed.isEnabled()
    tab.btn_auto_home_seq.click()
    tab.btn_edge_run.click()
    for panel in tab.axes.values():
        panel.btn_home.click()
        panel.btn_seek_home.click()
    assert auto_made == []
    assert edge_made == []
    assert home_made == []


def test_switch_map_unlocks_after_a_failure(tab, monkeypatch, tmp_path):
    fake, _ = _fake_worker(
        done_payload=(False, "B: switch map cancelled", []),
    )
    monkeypatch.setattr("rbl.gui.motor_tab.SwitchMapWorker", fake)
    _choose_axis(tab, "B")
    tab.btn_switch_run.click()
    assert "✗ B: switch map cancelled" in _console(tab)
    assert tab.btn_switch_run.text() == "Run switch map"
    assert tab.axes["B"].btn_move.isEnabled()
    assert tab.btn_auto_home_seq.isEnabled()
    assert tab.btn_edge_run.isEnabled()
    assert tab.spn_approach_speed.isEnabled()
    assert list(tmp_path.glob("switch_map_*.csv")) == []
