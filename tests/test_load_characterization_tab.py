"""
Tests for rbl.gui.load_characterization_tab.LoadCharacterizationTab.

No hardware: a bare Beamline() with no generators connected exercises the
"not connected" paths; the run lifecycle itself is covered end-to-end by
tests/test_load_characterizer.py, so these tests focus on the widget's own
wiring (mode/condition selection, table refresh, the _lj_tabs contract).
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from rbl.config import load_calibration_store as store
from rbl.config.calibration_config import LoadCondition
from rbl.gui.load_characterization_tab import LoadCharacterizationTab
from rbl.services.load_characterizer import Mode
from rbl.state.beamline import Beamline


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def tab(qapp):
    beamline = Beamline()
    return LoadCharacterizationTab(beamline)


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STORE_PATH", tmp_path / "load_calibration.json")
    return store


class TestModeAndConditionSelection:
    def test_defaults_to_mode_a_on_plates(self, tab):
        assert tab._selected_mode() == Mode.A
        assert tab._selected_load_condition() == LoadCondition.ON_PLATES

    def test_mode_b_selection(self, tab):
        tab.rb_mode_b.setChecked(True)
        assert tab._selected_mode() == Mode.B

    def test_mode_c_selection(self, tab):
        tab.rb_mode_c.setChecked(True)
        assert tab._selected_mode() == Mode.C

    def test_disconnected_condition_selection(self, tab):
        tab.rb_disconnected.setChecked(True)
        assert tab._selected_load_condition() == LoadCondition.DISCONNECTED


class TestRunGuards:
    def test_run_without_labjack_connection_warns_and_does_not_start(self, tab, monkeypatch):
        warned = []
        monkeypatch.setattr(
            "rbl.gui.load_characterization_tab.QMessageBox.warning",
            lambda *a, **k: warned.append(a))
        tab._on_run_clicked()
        assert warned
        assert tab._runner is None

    def test_run_without_generator_connected_warns(self, tab, monkeypatch):
        tab._connected = True
        warned = []
        monkeypatch.setattr(
            "rbl.gui.load_characterization_tab.QMessageBox.warning",
            lambda *a, **k: warned.append(a))
        tab._on_run_clicked()
        assert warned
        assert tab._runner is None

    def test_abort_with_no_runner_is_a_noop(self, tab):
        tab._on_abort_clicked()   # must not raise


class TestFourChannelTable:
    def test_unmeasured_channels_show_placeholder(self, tab, isolated_store):
        tab._refresh_table()
        for row in range(tab.table.rowCount()):
            assert tab.table.item(row, 1).text() == "—"

    def test_measured_channel_shows_values(self, tab, isolated_store):
        isolated_store.save_measurement("X+", c_pf=1180.0, g_us=0.02,
                                         load_condition="ON_PLATES", method="impedance_sweep")
        tab._refresh_table()
        row = [tab.table.item(r, 0).text() for r in range(tab.table.rowCount())].index("X+")
        assert tab.table.item(row, 1).text() == "1180.0"
        assert tab.table.item(row, 4).text() == "ON_PLATES"

    def test_outlier_conductance_is_flagged(self, tab, isolated_store):
        for label, g in (("X+", 0.01), ("X-", 0.01), ("Y+", 0.01), ("Y-", 5.0)):
            isolated_store.save_measurement(label, c_pf=1200.0, g_us=g,
                                             load_condition="ON_PLATES", method="m")
        tab._refresh_table()
        row = [tab.table.item(r, 0).text() for r in range(tab.table.rowCount())].index("Y-")
        item = tab.table.item(row, 0)
        assert item.background().color().alpha() > 0   # highlighted, not transparent


class TestLjTabsContract:
    def test_on_labjack_connected_updates_state(self, tab):
        tab.on_labjack_connected("T7-12345")
        assert tab._connected is True

    def test_on_labjack_disconnected_aborts_any_run(self, tab):
        class FakeRunner:
            def __init__(self):
                self.aborted = False
            def abort(self):
                self.aborted = True
        tab._runner = FakeRunner()
        tab.on_labjack_disconnected()
        assert tab._connected is False
        assert tab._runner.aborted is True

    def test_on_profile_changed_is_cached(self, tab):
        tab.on_profile_changed("AMP_PAIR")
        assert tab._current_profile == "AMP_PAIR"

    def test_on_window_forwards_to_runner(self, tab):
        received = []
        class FakeRunner:
            def on_window(self, payload):
                received.append(payload)
        tab._runner = FakeRunner()
        tab.on_window({"channels": {}})
        assert received == [{"channels": {}}]

    def test_shutdown_aborts_any_run(self, tab):
        class FakeRunner:
            def __init__(self):
                self.aborted = False
            def abort(self):
                self.aborted = True
        tab._runner = FakeRunner()
        tab.shutdown()
        assert tab._runner.aborted is True
