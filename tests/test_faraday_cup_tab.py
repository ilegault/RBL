"""
test_faraday_cup_tab.py
Headless GUI and cross-tab agreement tests for the Faraday Cup tab.

Covers:
  * Initial disconnected state: honest "—", "● Disconnected", no stale or zeroed values.
  * Connect / disconnect UI lifecycle through Beamline.
  * Live reading rendering with auto-scaling across nA, µA, mA via CupFeed.
  * Over-range indication: rendered explicitly as "OVER-RANGE", never as a number.
  * Unavailable / zero-check sentinel handling: rendered as "—".
  * Disconnection cleanup: resets display to "—", never leaving stale values.
  * Cross-tab agreement: slit currents and cup current are rendered as distinct,
    differently-labelled quantities; cup reading is not derived from or conflated
    with the log-amp path.
  * Assertions check operator-visible text, not private widget attributes.
"""
import csv
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from rbl.gui.app import MainWindow
from rbl.gui.faraday_cup_tab import FaradayCupTab
from rbl.hardware.current_monitor import format_current
from rbl.services.cup_session_writer import CupSessionWriter
from rbl.state.beamline import Beamline
from tests.payloads import CupFeed, window_payload


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch):
    """Stop QMessageBox.* from blocking the test on modal dialogs."""
    for name in ("information", "warning", "critical", "question"):
        monkeypatch.setattr(
            QMessageBox,
            name,
            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok),
        )


@pytest.fixture
def win(qapp):
    w = MainWindow()
    w.resize(1440, 920)
    w.show()
    qapp.processEvents()
    yield w
    w.close()
    qapp.processEvents()


class TestFaradayCupTabRendersCurrent:
    """Unit tests for FaradayCupTab rendering and lifecycle."""

    def test_initial_disconnected_state(self, qapp):
        beamline = Beamline()
        tab = FaradayCupTab(beamline=beamline)
        tab.show()
        qapp.processEvents()

        assert "Disconnected" in tab.status_pill.text()
        assert "—" in tab.lbl_current.text()
        assert "Not connected" in tab.lbl_detail.text()
        assert tab.btn_connect.text() == "Connect"

    def test_connect_if_needed(self, qapp, monkeypatch):
        beamline = Beamline()
        tab = FaradayCupTab(beamline=beamline)
        tab.show()
        qapp.processEvents()

        # Mock connect_picoammeter to avoid real VISA I/O in unit test
        called_resource = []
        monkeypatch.setattr(
            beamline,
            "connect_picoammeter",
            lambda res=None: called_resource.append(res),
        )

        status, detail = tab.connect_if_needed()
        assert status == "connected"
        assert len(called_resource) == 1

        # Simulate connected state
        monkeypatch.setattr(Beamline, "picoammeter_connected", property(lambda self: True))
        status2, detail2 = tab.connect_if_needed()
        assert status2 == "already"

    def test_renders_cup_current_autoscaling(self, qapp):
        beamline = Beamline()
        tab = FaradayCupTab(beamline=beamline)
        feed = CupFeed(tab, beamline=beamline)
        tab.show()
        qapp.processEvents()

        # 1. Nanoamps: 12.34 nA
        feed.send_reading(12.34e-9, timestamp=1.0)
        qapp.processEvents()
        assert "12.34 nA" in tab.lbl_current.text()
        assert "Connected" in tab.status_pill.text()

        # 2. Microamps: 5.67 µA
        feed.send_reading(5.67e-6, timestamp=2.0)
        qapp.processEvents()
        assert "5.67 µA" in tab.lbl_current.text()

        # 3. Milliamps: 1.25 mA
        feed.send_reading(1.25e-3, timestamp=3.0)
        qapp.processEvents()
        assert "1.25 mA" in tab.lbl_current.text()

    def test_over_range_rendered_as_text_not_number(self, qapp):
        beamline = Beamline()
        tab = FaradayCupTab(beamline=beamline)
        feed = CupFeed(tab, beamline=beamline)
        tab.show()
        qapp.processEvents()

        # Status word with over-range bit (bit 6 = 64) set
        feed.send_raw("+9.910000E+37,+1.000000,+00000064")
        qapp.processEvents()

        assert "OVER-RANGE" in tab.lbl_current.text()
        assert "9.9" not in tab.lbl_current.text()
        assert "Over-range" in tab.lbl_detail.text()

    def test_unavailable_reading_rendered_as_placeholder(self, qapp):
        beamline = Beamline()
        tab = FaradayCupTab(beamline=beamline)
        feed = CupFeed(tab, beamline=beamline)
        tab.show()
        qapp.processEvents()

        # Unavailable sentinel (+9.900000E+37)
        feed.send_raw("+9.900000E+37,+1.000000,+00000000")
        qapp.processEvents()

        assert "—" in tab.lbl_current.text()
        assert "unavailable" in tab.lbl_detail.text().lower()

    def test_disconnection_resets_view_without_stale_numbers(self, qapp):
        beamline = Beamline()
        tab = FaradayCupTab(beamline=beamline)
        feed = CupFeed(tab, beamline=beamline)
        tab.show()
        qapp.processEvents()

        feed.send_reading(2.5e-6, timestamp=1.0)
        qapp.processEvents()
        assert "2.50 µA" in tab.lbl_current.text()

        # Disconnect picoammeter
        beamline.disconnect_picoammeter()
        qapp.processEvents()

        assert "—" in tab.lbl_current.text()
        assert "2.50" not in tab.lbl_current.text()
        assert "Disconnected" in tab.status_pill.text()
        assert "Not connected" in tab.lbl_detail.text()


class TestCrossTabAgreementSlitVsCupCurrent:
    """Cross-tab agreement invariant tests (CONTEXT.md / ADR 0001).

    Asserts that slit current (intercepted by slit jaws) and cup current
    (transmitted to Faraday cup) are rendered as distinct, differently-labelled
    quantities, and that neither path conflates with or derives from the other.
    """

    def test_distinct_labels_and_screens(self, win):
        # Slit currents tab exists and is titled "Slit Currents"
        slit_idx = win.tab_index("Slit Currents")
        cup_idx = win.tab_index("Faraday Cup")
        assert cup_idx == slit_idx + 1

        slit_tab = win.current_tab
        cup_tab = win.faraday_cup_tab

        # Slit tab and cup tab are distinct widgets on distinct screens
        assert slit_tab is not cup_tab

    def test_logamp_stream_does_not_update_cup_tab(self, win, qapp):
        # Push 1 µA to all log-amp channels via LabJack window
        win.beamline.ingest_labjack_window(
            window_payload({"AIN0": 3.0, "AIN1": 3.0, "AIN2": 3.0, "AIN3": 3.0}, t=1.0)
        )
        qapp.processEvents()

        # Slit currents tab received and converted the log-amp window
        assert "1.00 µA" in win.current_tab.lbl_i["AIN0"].text()

        # Faraday Cup tab was NOT touched and remains disconnected / placeholder
        assert "—" in win.faraday_cup_tab.lbl_current.text()
        assert "1.00" not in win.faraday_cup_tab.lbl_current.text()
        assert "Disconnected" in win.faraday_cup_tab.status_pill.text()

    def test_cup_reading_does_not_update_slit_currents_tab(self, win, qapp):
        # Push 4.5 µA to Faraday cup
        feed = CupFeed(win.faraday_cup_tab, beamline=win.beamline)
        feed.send_reading(4.5e-6, timestamp=1.0)
        qapp.processEvents()

        # Faraday Cup tab renders the cup reading
        assert "4.50 µA" in win.faraday_cup_tab.lbl_current.text()

        # Slit currents tab remains unaffected
        for ain in ("AIN0", "AIN1", "AIN2", "AIN3"):
            assert "4.50" not in win.current_tab.lbl_i[ain].text()


class TestFaradayCupTabAcquisitionRuns:
    """Tests for threshold-triggered runs and force start/stop controls on FaradayCupTab."""

    def test_run_status_initial_disconnected(self, qapp):
        beamline = Beamline()
        tab = FaradayCupTab(beamline=beamline)
        tab.show()
        qapp.processEvents()

        assert "Disconnected" in tab.lbl_run_status.text()
        assert not tab.btn_force_start.isEnabled()
        assert not tab.btn_force_stop.isEnabled()

    def test_run_status_connected_idle(self, qapp):
        beamline = Beamline()
        tab = FaradayCupTab(beamline=beamline)
        feed = CupFeed(tab, beamline=beamline)
        tab.show()
        qapp.processEvents()

        # Send reading below arm threshold (0.1 µA < 0.5 µA)
        feed.send_reading(0.1e-6, timestamp=0.0, t_host=0.0)
        qapp.processEvents()

        assert "Idle" in tab.lbl_run_status.text()
        assert tab.btn_force_start.isEnabled()
        assert not tab.btn_force_stop.isEnabled()

    def test_threshold_triggered_acquisition_and_release(self, qapp):
        beamline = Beamline()
        tab = FaradayCupTab(beamline=beamline)
        feed = CupFeed(tab, beamline=beamline)
        tab.show()
        qapp.processEvents()

        # Insertion begins at t=1.0 with 1.0 µA
        feed.send_reading(1.0e-6, timestamp=1.0, t_host=1.0)
        qapp.processEvents()
        # Debounce pending
        assert "In Beam" in tab.lbl_run_status.text() or "Idle" in tab.lbl_run_status.text()

        # At t=2.0 (>= 1.0s debounce elapsed), run 1 opens
        feed.send_reading(1.0e-6, timestamp=2.0, t_host=2.0)
        qapp.processEvents()

        assert "ACQUIRING (Run #1)" in tab.lbl_run_status.text()
        assert not tab.btn_force_start.isEnabled()
        assert tab.btn_force_stop.isEnabled()
        assert beamline.cup_acquiring

        # Cup withdrawn at t=10.0 (0.0 A)
        feed.send_reading(0.0, timestamp=10.0, t_host=10.0)
        qapp.processEvents()
        # Still acquiring during release interval
        assert "ACQUIRING" in tab.lbl_run_status.text()

        # At t=13.0 (>= 3.0s release elapsed), run 1 closes
        feed.send_reading(0.0, timestamp=13.0, t_host=13.0)
        qapp.processEvents()

        assert "Idle" in tab.lbl_run_status.text()
        assert tab.btn_force_start.isEnabled()
        assert not tab.btn_force_stop.isEnabled()
        assert not beamline.cup_acquiring

    def test_force_start_and_force_stop_buttons(self, qapp):
        beamline = Beamline()
        tab = FaradayCupTab(beamline=beamline)
        feed = CupFeed(tab, beamline=beamline)
        tab.show()
        qapp.processEvents()

        feed.send_reading(0.0, timestamp=0.0, t_host=0.0)
        qapp.processEvents()
        assert tab.btn_force_start.isEnabled()

        # Click Force Start
        tab.btn_force_start.click()
        qapp.processEvents()

        assert "ACQUIRING (Run #1)" in tab.lbl_run_status.text()
        assert not tab.btn_force_start.isEnabled()
        assert tab.btn_force_stop.isEnabled()
        assert beamline.cup_acquiring

        # Click Force Stop
        tab.btn_force_stop.click()
        qapp.processEvents()

        assert "Idle" in tab.lbl_run_status.text()
        assert tab.btn_force_start.isEnabled()
        assert not tab.btn_force_stop.isEnabled()
        assert not beamline.cup_acquiring

    def test_disconnection_mid_run_cleans_up_tab(self, qapp):
        beamline = Beamline()
        tab = FaradayCupTab(beamline=beamline)
        feed = CupFeed(tab, beamline=beamline)
        tab.show()
        qapp.processEvents()

        feed.send_reading(1.0e-6, timestamp=0.0, t_host=0.0)
        feed.send_reading(1.0e-6, timestamp=1.0, t_host=1.0)
        qapp.processEvents()
        assert "ACQUIRING" in tab.lbl_run_status.text()

        # Disconnect mid-run
        beamline.disconnect_picoammeter()
        qapp.processEvents()

        assert "Disconnected" in tab.lbl_run_status.text()
        assert not tab.btn_force_start.isEnabled()
        assert not tab.btn_force_stop.isEnabled()
        assert not beamline.cup_acquiring


class TestFaradayCupTabRunMetricsAndAverage:
    """Tests for active run duration, sample counts, running average, and over-range exclusion."""

    def test_run_duration_and_samples_updated_during_run(self, qapp):
        beamline = Beamline()
        tab = FaradayCupTab(beamline=beamline)
        feed = CupFeed(tab, beamline=beamline)
        tab.show()
        qapp.processEvents()

        # Reading at t_host=10.0 (debouncing starts)
        feed.send_reading(1.0e-6, timestamp=1.0, t_host=10.0)
        qapp.processEvents()

        # Reading at t_host=11.0 (>= 1.0 s debounce -> Run 1 opens at t_host=11.0)
        feed.send_reading(1.0e-6, timestamp=2.0, t_host=11.0)
        qapp.processEvents()
        assert "ACQUIRING" in tab.lbl_run_status.text()

        # Send subsequent samples at t_host=12.0, 13.0, 14.0
        feed.send_reading(1.0e-6, timestamp=3.0, t_host=12.0)
        feed.send_reading(1.0e-6, timestamp=4.0, t_host=13.0)
        feed.send_reading(1.0e-6, timestamp=5.0, t_host=14.0)
        qapp.processEvents()

        # Duration is 14.0 - 11.0 = 3.0 s, 4 samples total in run
        assert "3.0 s" in tab.lbl_duration.text()
        assert tab.lbl_samples.text() == "4"

    def test_running_average_agrees_with_session_file(self, qapp, tmp_path):
        """Running average matches the average computed from logged session file."""
        beamline = Beamline()
        session_writer = CupSessionWriter(output_dir=tmp_path)
        tab = FaradayCupTab(beamline=beamline, session_writer=session_writer)
        feed = CupFeed(tab, beamline=beamline)
        tab.show()
        qapp.processEvents()

        feed.send_reading(0.0, timestamp=0.0, t_host=0.0)
        qapp.processEvents()

        # Force start run
        tab.btn_force_start.click()
        qapp.processEvents()

        # Send series: 1.0 µA, 2.0 µA, 3.0 µA
        feed.send_reading(1.0e-6, timestamp=1.0, t_host=1.0)
        feed.send_reading(2.0e-6, timestamp=2.0, t_host=2.0)
        feed.send_reading(3.0e-6, timestamp=3.0, t_host=3.0)
        qapp.processEvents()

        # Capture displayed running average while run is open
        displayed_average = tab.lbl_average.text()
        assert "2.00 µA" in displayed_average

        # Close run and file
        tab.btn_force_stop.click()
        tab.close()
        qapp.processEvents()

        # Read the logged session CSV file and compute average independently
        with open(session_writer.csv_path, encoding="utf-8") as f:
            reader = csv.DictReader([line for line in f if not line.startswith("#")])
            rows = list(reader)

        sample_rows = [r for r in rows if r["record_type"] == "sample" and r["run_id"] == "1"]
        assert len(sample_rows) == 3
        valid_currents = [
            float(r["current_a"]) for r in sample_rows if r["over_range"] == "False"
        ]
        file_average = sum(valid_currents) / len(valid_currents)

        assert file_average == pytest.approx(2.0e-6)
        # Displayed text and computed file average must agree
        assert displayed_average == format_current(file_average)

    def test_over_range_samples_excluded_from_average_and_visibly_flagged(self, qapp):
        """Over-range samples are excluded from average calculation and flagged on UI."""
        beamline = Beamline()
        tab = FaradayCupTab(beamline=beamline)
        feed = CupFeed(tab, beamline=beamline)
        tab.show()
        qapp.processEvents()

        feed.send_reading(0.0, timestamp=0.0, t_host=0.0)
        qapp.processEvents()

        tab.btn_force_start.click()
        qapp.processEvents()

        # 2 normal samples (4.0 µA, 6.0 µA) -> average = 5.0 µA
        feed.send_reading(4.0e-6, timestamp=1.0, t_host=1.0)
        feed.send_reading(6.0e-6, timestamp=2.0, t_host=2.0)
        qapp.processEvents()
        assert "5.00 µA" in tab.lbl_average.text()

        # 1 over-range sample (+9.91e37 with status word 0x40)
        feed.send_raw("+9.910000E+37,+1.000000,+00000064", t_host=3.0)
        qapp.processEvents()

        # Running average remains 5.00 µA (over-range excluded)
        assert "5.00 µA" in tab.lbl_average.text()

        # Total samples and over-range exclusion are explicitly visible
        assert "1 over-range excluded" in tab.lbl_samples.text()
        assert "1 over-range excluded" in tab.lbl_avg_detail.text()
        assert "2 valid samples" in tab.lbl_avg_detail.text()

    def test_disconnected_tab_metrics_are_clean_and_not_zeroed(self, qapp):
        beamline = Beamline()
        tab = FaradayCupTab(beamline=beamline)
        tab.show()
        qapp.processEvents()

        assert "—" in tab.lbl_average.text()
        assert "—" in tab.lbl_duration.text()
        assert "—" in tab.lbl_samples.text()
        assert "Not connected" in tab.lbl_avg_detail.text()
        assert "0" not in tab.lbl_average.text()

    def test_mid_run_disconnection_resets_metrics_honestly(self, qapp):
        beamline = Beamline()
        tab = FaradayCupTab(beamline=beamline)
        feed = CupFeed(tab, beamline=beamline)
        tab.show()
        qapp.processEvents()

        feed.send_reading(1.0e-6, timestamp=0.0, t_host=0.0)
        feed.send_reading(1.0e-6, timestamp=1.0, t_host=1.0)
        qapp.processEvents()
        assert "ACQUIRING" in tab.lbl_run_status.text()
        assert "1.00 µA" in tab.lbl_average.text()

        # Disconnect mid-run
        beamline.disconnect_picoammeter()
        qapp.processEvents()

        assert "Disconnected" in tab.lbl_run_status.text()
        assert "—" in tab.lbl_average.text()
        assert "—" in tab.lbl_duration.text()
        assert "—" in tab.lbl_samples.text()
        assert "Not connected" in tab.lbl_avg_detail.text()


class TestFaradayCupTabLivePlot:
    """Tests for FaradayCupTab live scrolling plot, navigation, and zoom controls."""

    def test_live_plot_receives_data_and_navigation_controls(self, qapp):
        beamline = Beamline()
        tab = FaradayCupTab(beamline=beamline)
        feed = CupFeed(tab, beamline=beamline)
        tab.show()
        qapp.processEvents()

        feed.send_reading(1.5e-6, timestamp=1.0, t_host=1.0)
        feed.send_reading(2.5e-6, timestamp=2.0, t_host=2.0)
        qapp.processEvents()

        latest_t, latest_v = tab.buffer.latest()
        assert latest_t == 2.0
        assert latest_v == pytest.approx(2.5e-6)

        assert tab.plot.is_live
        assert "● LIVE" in tab.lbl_mode.text()
        assert not tab.btn_jump_live.isVisible()

        # Zoom out
        initial_window = tab.plot.window_seconds
        tab.plot.zoom_out()
        qapp.processEvents()
        assert tab.plot.window_seconds > initial_window
        assert "LIVE" in tab.lbl_mode.text()

        # Zoom in
        tab.plot.zoom_in()
        qapp.processEvents()
        assert tab.plot.window_seconds == initial_window

        # Drag slider into frozen mode
        tab.plot.slider.setValue(5000)
        qapp.processEvents()
        assert not tab.plot.is_live
        assert tab.btn_jump_live.isVisible()
        assert "Frozen" in tab.lbl_mode.text()

        # Click Jump to Live
        tab.btn_jump_live.click()
        qapp.processEvents()
        assert tab.plot.is_live
        assert not tab.btn_jump_live.isVisible()
        assert "● LIVE" in tab.lbl_mode.text()

    def test_show_hide_controls_redraw_timer(self, qapp):
        beamline = Beamline()
        tab = FaradayCupTab(beamline=beamline)
        feed = CupFeed(tab, beamline=beamline)
        tab.show()
        qapp.processEvents()

        feed.send_reading(1.0e-6, timestamp=1.0, t_host=1.0)
        qapp.processEvents()
        assert tab.plot.redraw_timer.isActive()

        # Hide tab
        tab.hide()
        qapp.processEvents()
        assert not tab.plot.redraw_timer.isActive()

        # Show tab again
        tab.show()
        qapp.processEvents()
        assert tab.plot.redraw_timer.isActive()

    def test_disconnection_clears_plot_trace(self, qapp):
        beamline = Beamline()
        tab = FaradayCupTab(beamline=beamline)
        feed = CupFeed(tab, beamline=beamline)
        tab.show()
        qapp.processEvents()

        feed.send_reading(3.0e-6, timestamp=1.0, t_host=1.0)
        qapp.processEvents()

        # Disconnect
        beamline.disconnect_picoammeter()
        qapp.processEvents()

        assert "—" in tab.lbl_current.text()
        assert not tab.plot.redraw_timer.isActive()


