"""
Stream contract tests over the declared tab list.

Four rules, each stated once and run against every tab that consumes stream
windows, parametrized from the single tab declaration (TAB_DECLARATIONS where
consumes_stream=True) so that a tab added later is covered without anyone
remembering to add tests.

The rules:
1. A tab survives a window in which its channels are absent — the marker a
   paused readout produces. A paused readout must not blank or crash a screen.
2. A tab survives non-finite values (NaN / Inf / -Inf). A disconnected instrument
   must not take down the interface.
3. A tab takes only its own channels from a shared window (channel isolation
   invariant). One screen cannot consume another's data.
4. A tab shuts down cleanly. Closing the application with hardware disconnected
   must not hang.
"""
import math
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

import rbl.config.hardware_config as SC
from rbl.gui.app import TAB_DECLARATIONS, MainWindow, TabDeclaration
from tests.payloads import FULL_READING, LabJackFeed, window_payload

# The single parametrization source for all stream contract tests
STREAM_CONSUMING_DECLARATIONS: list[TabDeclaration] = [
    decl for decl in TAB_DECLARATIONS if decl.consumes_stream
]


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch):
    """Prevent modal dialogs from blocking headless contract test runs."""
    for name in ("information", "warning", "critical", "question"):
        monkeypatch.setattr(
            QMessageBox,
            name,
            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok),
        )


@pytest.fixture
def win(qapp):
    """Instantiate a real MainWindow with all declared tabs and real Beamline."""
    w = MainWindow()
    w.resize(1440, 920)
    w.show()
    qapp.processEvents()
    yield w
    w.close()
    qapp.processEvents()


@pytest.fixture
def stream_tab(decl, win):
    """Retrieve the declared stream-consuming tab instance from MainWindow."""
    return getattr(win, decl.attr_name)


@pytest.fixture
def feed(win):
    """A LabJackFeed wired to the application's real Beamline and tabs."""
    return LabJackFeed(*win.stream_consuming_tabs, beamline=win.beamline)


# ─── Contract Tests ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("decl", STREAM_CONSUMING_DECLARATIONS, ids=lambda d: d.title)
class TestStreamContracts:
    """The four stream contract rules parametrized over all stream-consuming tabs."""

    def test_tab_survives_absent_channels(self, decl, win, stream_tab, feed, qapp):
        """Rule 1: A tab survives a window in which its channels are absent — the
        marker a paused readout produces. A paused readout must not blank or crash a screen."""
        win.beamline.labjack_connected.emit("TESTSERIAL")
        qapp.processEvents()

        # Feed a normal full reading first
        feed.send(FULL_READING, t=1.0)
        qapp.processEvents()

        # Feed an empty window with all channels absent / None
        feed.send_payload(window_payload({}, t=2.0))
        qapp.processEvents()

        # Tab widget must remain alive and responsive
        assert stream_tab.isVisibleTo(win) or not stream_tab.isHidden()

        # If tab renders readouts, assert paused/absent indicator is properly displayed
        if decl.attr_name == "current_tab":
            assert "Waveform" in stream_tab.lbl_i["AIN0"].text()
        elif decl.attr_name == "amp_tab":
            assert stream_tab.lbl_meas["X+"].text() == "—"

        # Trigger redraw if the tab has a redraw plot mechanism
        if hasattr(stream_tab, "_redraw_plot"):
            stream_tab._redraw_plot()
        if hasattr(stream_tab, "plot") and hasattr(stream_tab.plot, "canvas"):
            stream_tab.plot.canvas.draw()
        qapp.processEvents()

        # Tab can recover seamlessly when normal stream window resumes
        feed.send(FULL_READING, t=3.0)
        qapp.processEvents()
        if hasattr(stream_tab, "_redraw_plot"):
            stream_tab._redraw_plot()

    def test_tab_survives_non_finite_values(self, decl, win, stream_tab, feed, qapp):
        """Rule 2: A tab survives non-finite values (NaN / Inf / -Inf).
        A disconnected instrument must not take down the interface."""
        win.beamline.labjack_connected.emit("TESTSERIAL")
        qapp.processEvents()

        # Test NaN payload
        nan_dict = {ain: float("nan") for ain in SC.LABJACK_CHANNEL_MAP}
        nan_dict.update({ain: float("nan") for ain in SC.AMP_AIN_NAMES})
        feed.send(nan_dict, t=1.0)
        qapp.processEvents()

        # Test +Inf payload
        inf_dict = {ain: float("inf") for ain in SC.LABJACK_CHANNEL_MAP}
        inf_dict.update({ain: float("inf") for ain in SC.AMP_AIN_NAMES})
        feed.send(inf_dict, t=2.0)
        qapp.processEvents()

        # Test -Inf payload
        neginf_dict = {ain: float("-inf") for ain in SC.LABJACK_CHANNEL_MAP}
        neginf_dict.update({ain: float("-inf") for ain in SC.AMP_AIN_NAMES})
        feed.send(neginf_dict, t=3.0)
        qapp.processEvents()

        # Redraw / rendering must not raise on non-finite data
        if hasattr(stream_tab, "_redraw_plot"):
            stream_tab._redraw_plot()
        if hasattr(stream_tab, "plot") and hasattr(stream_tab.plot, "canvas"):
            stream_tab.plot.canvas.draw()
        qapp.processEvents()

    def test_tab_takes_only_own_channels(self, decl, win, stream_tab, feed, qapp):
        """Rule 3: A tab takes only its own channels from a shared window (channel
        isolation invariant). One screen cannot consume another's data."""
        logamp_ains = set(SC.LABJACK_CHANNEL_MAP.keys())
        amp_ains = set(SC.AMP_AIN_NAMES)

        if hasattr(stream_tab, "buffers"):
            tab_buffer_keys = set(stream_tab.buffers.keys())
            # CurrentTab must buffer only log amp channels; AmpTab must buffer only amp channels
            if decl.attr_name == "current_tab":
                assert tab_buffer_keys.issubset(logamp_ains)
                assert tab_buffer_keys.isdisjoint(amp_ains)
            elif decl.attr_name == "amp_tab":
                assert tab_buffer_keys.issubset(amp_ains)
                assert tab_buffer_keys.isdisjoint(logamp_ains)

        # Send a window containing ONLY the foreign subsystem's channels
        if decl.attr_name == "current_tab":
            # Send only amplifier channels to CurrentTab
            foreign_volts = {"AIN6": 0.5, "AIN7": -2.0, "AIN12": 1.0, "AIN13": 3.0}
            feed.send(foreign_volts, t=10.0)
            qapp.processEvents()
            # CurrentTab buffers should not have received any non-empty points from foreign channels
            for ain in logamp_ains:
                t, val = stream_tab.buffers[ain].latest()
                assert math.isnan(val) or t != 10.0
        elif decl.attr_name == "amp_tab":
            # Send only log amp channels to AmpTab
            foreign_volts = {"AIN0": 3.0, "AIN1": 3.0, "AIN2": 3.0, "AIN3": 3.0}
            feed.send(foreign_volts, t=10.0)
            qapp.processEvents()
            # AmpTab buffers should not have received any points from foreign channels
            for ain in amp_ains:
                t, val = stream_tab.buffers[ain].latest()
                assert math.isnan(val) or t != 10.0

        # Send a mixed window and ensure each tab extracts only its designated values
        feed.send(FULL_READING, t=20.0)
        qapp.processEvents()

        if decl.attr_name == "current_tab":
            t, i = stream_tab.buffers["AIN0"].latest()
            assert t == 20.0
            assert abs(i - 1e-6) < 1e-9
        elif decl.attr_name == "amp_tab":
            t, kv = stream_tab.buffers["AIN13"].latest()
            assert t == 20.0
            assert abs(kv - 3.0) < 1e-9

    def test_tab_shuts_down_cleanly(self, decl, win, stream_tab, qapp):
        """Rule 4: A tab shuts down cleanly. Closing the application with hardware
        disconnected must not hang."""
        # Tab shutdown must be callable and finish cleanly
        if hasattr(stream_tab, "shutdown"):
            stream_tab.shutdown()

        # Timers must be stopped
        if hasattr(stream_tab, "plot") and hasattr(stream_tab.plot, "redraw_timer"):
            assert not stream_tab.plot.redraw_timer.isActive()

        # Multiple shutdowns must be safe (idempotent)
        if hasattr(stream_tab, "shutdown"):
            stream_tab.shutdown()

        qapp.processEvents()
