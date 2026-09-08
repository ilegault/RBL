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
        tab._on_run_clicked()
        assert warned
        assert tab._trial is None

    def test_run_without_pot_position_warns(self, tab, monkeypatch):
        tab._connected = True
        warned = []
        monkeypatch.setattr(
            "rbl.gui.dynamic_adjustment_tab.QMessageBox.warning",
            lambda *a, **k: warned.append(a))
        tab._on_run_clicked()
        assert warned
        assert tab._trial is None

    def test_run_with_whitespace_only_pot_position_warns(self, tab, monkeypatch):
        tab._connected = True
        tab.le_pot_position.setText("   ")
        warned = []
        monkeypatch.setattr(
            "rbl.gui.dynamic_adjustment_tab.QMessageBox.warning",
            lambda *a, **k: warned.append(a))
        tab._on_run_clicked()
        assert warned
        assert tab._trial is None

    def test_run_without_generator_connected_warns(self, tab, monkeypatch):
        tab._connected = True
        tab.le_pot_position.setText("2 o'clock")
        warned = []
        monkeypatch.setattr(
            "rbl.gui.dynamic_adjustment_tab.QMessageBox.warning",
            lambda *a, **k: warned.append(a))
        tab._on_run_clicked()
        assert warned
        assert tab._trial is None


class TestTable:
    def test_no_trials_shows_placeholder_hint(self, tab, isolated_history):
        tab._refresh_table()
        assert "No trials" in tab.lbl_hint.text()

    def test_trials_populate_the_table_and_winner_hint(self, tab, isolated_history):
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
        tab.cb_channel.setCurrentText("X+")
        tab._refresh_table()
        assert tab.table.rowCount() == 2
        assert "2 o'clock" in tab.lbl_hint.text()

    def test_large_creep_winner_suggests_a_direction(self, tab, isolated_history):
        isolated_history.append_trial({
            "amp_label": "X+", "pot_position": "4 o'clock", "figure_of_merit": 1.0,
            "voltage": {"overshoot_pct": 0.5, "flat_top_creep_pct": 3.0, "settling_1pct_s": 0.0005},
            "current": {"peak_current_ma": 8.0},
        })
        tab.cb_channel.setCurrentText("X+")
        tab._refresh_table()
        assert "increase" in tab.lbl_hint.text() or "decrease" in tab.lbl_hint.text()


class TestLjTabsContract:
    def test_on_labjack_connected_updates_state(self, tab):
        tab.on_labjack_connected("T7-12345")
        assert tab._connected is True

    def test_on_labjack_disconnected_updates_state(self, tab):
        tab.on_labjack_connected("T7-12345")
        tab.on_labjack_disconnected()
        assert tab._connected is False

    def test_on_profile_changed_is_cached(self, tab):
        tab.on_profile_changed("SINGLE_FAST")
        assert tab._current_profile == "SINGLE_FAST"

    def test_on_window_forwards_to_trial(self, tab):
        received = []
        class FakeTrial:
            def on_window(self, payload):
                received.append(payload)
        tab._trial = FakeTrial()
        tab.on_window({"channels": {}})
        assert received == [{"channels": {}}]

    def test_shutdown_does_not_raise(self, tab):
        tab.shutdown()
