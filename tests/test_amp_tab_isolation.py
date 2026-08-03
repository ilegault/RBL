"""
The non-interference guarantee, asserted at the tab level.

Both tabs are fed from the SAME stream window on the SAME shared T7. Each
must consume only its own AINs and leave the other's alone.

The window now reaches them as two typed snapshots — a LogAmpState and an
AmpState, split and converted by Beamline — rather than as a raw dict each
tab picked over itself. That is what these tests drive (see tests/payloads.py):
the guarantee is unchanged, but it is now structural, since neither tab is
handed the other's channels at all.
"""
import os
import math

import pytest

if "DISPLAY" not in os.environ and "QT_QPA_PLATFORM" not in os.environ:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication

from rbl.config import hardware_config as SC
from tests.payloads import LabJackFeed


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


# A single window covering every channel, as the shared stream worker emits it.
# Channel map (rbl/config/hardware_config.py): log amps AIN0-3, spare AIN4-5,
# then amp monitors as (current, voltage) pairs Y-(6,7) Y+(8,9) X-(10,11) X+(12,13).
FULL_READING = {
    "AIN0": 3.0, "AIN1": 3.0, "AIN2": 3.0, "AIN3": 3.0,   # log amps -> 1 µA each
    "AIN6":  0.5, "AIN7":  -2.0,     # Y- :  5 mA, -2 kV
    "AIN8":  0.5, "AIN9":   2.0,     # Y+ :  5 mA,  2 kV
    "AIN10": 1.0, "AIN11": -3.0,     # X- : 10 mA, -3 kV
    "AIN12": 1.0, "AIN13":  3.0,     # X+ : 10 mA,  3 kV
}


class TestAmpTabIgnoresLogAmps:
    @pytest.fixture
    def amp(self, qapp):
        from rbl.gui.amp_tab import AmpTab
        t = AmpTab()
        yield t
        t.shutdown()

    @pytest.fixture
    def feed(self, amp):
        return LabJackFeed(amp)

    def test_buffers_only_amp_channels(self, amp):
        assert set(amp.buffers.keys()) == set(SC.AMP_AIN_NAMES)
        for log_ain in SC.LABJACK_CHANNEL_MAP.keys():
            assert log_ain not in amp.buffers

    def test_window_populates_only_amp_buffers(self, amp, feed):
        feed.send(FULL_READING)
        for ain in SC.AMP_AIN_NAMES:
            t, v = amp.buffers[ain].latest()
            assert not math.isnan(v), f"{ain} buffer empty"

    def test_voltage_conversion(self, amp, feed):
        feed.send(FULL_READING)
        _, kv = amp.buffers["AIN13"].latest()    # X+ voltage
        assert abs(kv - 3.0) < 1e-9

    def test_current_conversion(self, amp, feed):
        feed.send(FULL_READING)
        _, ma = amp.buffers["AIN12"].latest()    # X+ current
        assert abs(ma - 10.0) < 1e-9

    def test_negative_rail_reads_as_magnitude(self, amp, feed):
        """The trend buffer holds RMS kV, which is a magnitude.

        The stream worker reports peak and RMS as magnitudes (peak is
        max|sample| over the window), so a plate held at -3 kV trends at
        +3.000 kV. The sign is not lost to the app — it is in the signed
        per-sample waveform, which is what the scope view below 1 s and the
        Overview's pair trace both draw, and it is those that answer "which
        way is this plate driven?". The trend line answers "how hard?".
        """
        feed.send(FULL_READING)
        _, kv = amp.buffers["AIN11"].latest()    # X- voltage, held at -3 V
        assert abs(kv - 3.0) < 1e-9

    def test_starts_live(self, amp):
        assert amp.plot.is_live is True
        assert amp.plot.slider.value() == 10_000

    def test_slider_enters_frozen(self, amp, feed):
        for i in range(5):
            feed.send(FULL_READING, t=float(i))
        amp.plot._on_slider_changed(4000)
        assert amp.plot.is_live is False
        assert amp.plot.frozen_right_edge is not None

    def test_jump_to_live(self, amp, feed):
        for i in range(5):
            feed.send(FULL_READING, t=float(i))
        amp.plot._on_slider_changed(4000)
        amp.plot.jump_to_live()
        assert amp.plot.is_live is True

    def test_redraw_empty_is_safe(self, amp):
        amp._redraw_plot()   # must not raise


class TestCurrentTabIgnoresAmps:
    @pytest.fixture
    def cur(self, qapp):
        from rbl.gui.logamp_tab import CurrentTab
        t = CurrentTab()
        yield t
        t.shutdown()

    @pytest.fixture
    def feed(self, cur):
        return LabJackFeed(cur)

    def test_buffers_only_log_amp_channels(self, cur):
        assert set(cur.buffers.keys()) == set(SC.LABJACK_CHANNEL_MAP.keys())
        for amp_ain in SC.AMP_AIN_NAMES:
            assert amp_ain not in cur.buffers

    def test_log_amp_math_unchanged_with_twelve_channels(self, cur, feed):
        """The regression that matters: a window carrying all 12 channels must
        not change what the log-amp tab shows. 3.0 V -> 1 µA, as before."""
        feed.send(FULL_READING)
        _, i = cur.buffers["AIN0"].latest()
        assert abs(i - 1e-6) < 1e-9
        assert "µA" in cur.lbl_i["AIN0"].text()

    def test_centering_still_works(self, cur, feed):
        feed.send(FULL_READING)
        # No Galil in this fixture, so the indicator reports raw imbalance.
        assert abs(cur.beam_indicator.view.ratio_x) < 1e-9


class TestBothTabsShareOneWindow:
    def test_same_window_feeds_both_correctly(self, qapp):
        from rbl.gui.amp_tab import AmpTab
        from rbl.gui.logamp_tab import CurrentTab
        amp = AmpTab()
        cur = CurrentTab()
        try:
            # Exactly what MainWindow does: one window, one Beamline, two
            # snapshots, one to each tab.
            feed = LabJackFeed(amp, cur)
            feed.send(FULL_READING)

            _, kv = amp.buffers["AIN13"].latest()
            assert abs(kv - 3.0) < 1e-9        # amp tab got its channel

            _, i = cur.buffers["AIN0"].latest()
            assert abs(i - 1e-6) < 1e-9        # log-amp tab got its channel
        finally:
            amp.shutdown()
            cur.shutdown()
