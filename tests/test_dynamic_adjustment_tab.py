"""
Tests for rbl.gui.dynamic_adjustment_tab.DynamicAdjustmentTab.

No hardware: a bare Beamline() with no generators connected exercises the
"not connected"/"pot position required" guard paths; the trial pipeline
itself is covered end-to-end by tests/test_dynamic_adjustment.py.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from rbl.gui.dynamic_adjustment_tab import DynamicAdjustmentTab
from rbl.services import dynamic_adjustment_history as history
from rbl.state.beamline import Beamline


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def tab(qapp):
    return DynamicAdjustmentTab(Beamline())


@pytest.fixture
def isolated_history(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "DYNAMIC_ADJUSTMENT_HISTORY_PATH",
                         tmp_path / "dynamic_adjustment_history.jsonl")
    import rbl.gui.dynamic_adjustment_tab as tab_module
    monkeypatch.setattr(tab_module, "append_trial", history.append_trial)
    monkeypatch.setattr(tab_module, "trials_for", history.trials_for)
    monkeypatch.setattr(tab_module, "winner_for", history.winner_for)
    return history


class TestRunGuards:
    def test_run_without_labjack_connection_warns(self, tab, monkeypatch):
        warned = []
        monkeypatch.setattr(
            "rbl.gui.dynamic_adjustment_tab.QMessageBox.warning",
            lambda *a, **k: warned.append(a))
        tab.le_pot_position.setText("2 o'clock")
        tab.btn_run.click()
        assert warned

    def test_run_without_pot_position_warns(self, tab, monkeypatch):
        tab.on_labjack_connected("T7-12345")
        warned = []
        monkeypatch.setattr(
            "rbl.gui.dynamic_adjustment_tab.QMessageBox.warning",
            lambda *a, **k: warned.append(a))
        tab.btn_run.click()
        assert warned

    def test_run_with_whitespace_only_pot_position_warns(self, tab, monkeypatch):
        tab.on_labjack_connected("T7-12345")
        tab.le_pot_position.setText("   ")
        warned = []
        monkeypatch.setattr(
            "rbl.gui.dynamic_adjustment_tab.QMessageBox.warning",
            lambda *a, **k: warned.append(a))
        tab.btn_run.click()
        assert warned

    def test_run_without_generator_connected_warns(self, tab, monkeypatch):
        tab.on_labjack_connected("T7-12345")
        tab.le_pot_position.setText("2 o'clock")
        warned = []
        monkeypatch.setattr(
            "rbl.gui.dynamic_adjustment_tab.QMessageBox.warning",
            lambda *a, **k: warned.append(a))
        tab.btn_run.click()
        assert warned


class TestTable:
    def test_no_trials_shows_placeholder_hint(self, isolated_history):
        tab = DynamicAdjustmentTab(Beamline())
        assert "No trials" in tab.lbl_hint.text()

    def test_trials_populate_the_table_and_winner_hint(self, isolated_history):
        isolated_history.append_trial({
            "amp_label": "X+", "pot_position": "1 o'clock", "figure_of_merit": 5.0,
            "voltage": {"overshoot_pct": 3.0, "flat_top_creep_pct": 1.0, "settling_1pct_s": 0.001},
            "current": {"peak_current_ma": 10.0},
        })
        isolated_history.append_trial({
            "amp_label": "X+", "pot_position": "2 o'clock", "figure_of_merit": 1.0,
            "voltage": {"overshoot_pct": 0.5, "flat_top_creep_pct": 0.1, "settling_1pct_s": 0.0005},
            "current": {"peak_current_ma": 8.0},
        })
        tab = DynamicAdjustmentTab(Beamline())
        assert tab.table.rowCount() == 2
        assert "2 o'clock" in tab.lbl_hint.text()

    def test_large_creep_winner_suggests_a_direction(self, isolated_history):
        isolated_history.append_trial({
            "amp_label": "X+", "pot_position": "4 o'clock", "figure_of_merit": 1.0,
            "voltage": {"overshoot_pct": 0.5, "flat_top_creep_pct": 3.0, "settling_1pct_s": 0.0005},
            "current": {"peak_current_ma": 8.0},
        })
        tab = DynamicAdjustmentTab(Beamline())
        assert "increase" in tab.lbl_hint.text() or "decrease" in tab.lbl_hint.text()



