"""
The non-interference guarantee, asserted at the tab level.

Both tabs receive the SAME 12-channel dict from the SAME shared poll worker.
Each must consume only its own AINs and leave the other's alone.
"""
import os
import math

import pytest

if "DISPLAY" not in os.environ and "QT_QPA_PLATFORM" not in os.environ:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication

from rbl.config import hardware_config as SC


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


# A single reading covering every channel, as the shared worker emits it.
FULL_READING = {
    "AIN0": 3.0, "AIN1": 3.0, "AIN2": 3.0, "AIN3": 3.0,   # log amps -> 1 µA each
    "AIN4":  3.0, "AIN5":  1.0,     # X+ :  3 kV, 10 mA
    "AIN6": -3.0, "AIN7":  1.0,     # X- : -3 kV, 10 mA
    "AIN8":  2.0, "AIN9":  0.5,     # Y+ :  2 kV,  5 mA
    "AIN10": -2.0, "AIN11": 0.5,    # Y- : -2 kV,  5 mA
}


class TestAmpTabIgnoresLogAmps:
    @pytest.fixture
    def amp(self, qapp):
        from amp_tab import AmpTab
        t = AmpTab()
        yield t
        t.shutdown()

    def test_buffers_only_amp_channels(self, amp):
        assert set(amp.buffers.keys()) == set(SC.AMP_AIN_NAMES)
        for log_ain in SC.LABJACK_CHANNEL_MAP.keys():
            assert log_ain not in amp.buffers

    def test_reading_populates_only_amp_buffers(self, amp):
        amp._on_reading(1.0, FULL_READING)
        for ain in SC.AMP_AIN_NAMES:
            t, v = amp.buffers[ain].latest()
            assert not math.isnan(v), f"{ain} buffer empty"

    def test_voltage_conversion(self, amp):
        amp._on_reading(1.0, FULL_READING)
        _, kv = amp.buffers["AIN4"].latest()     # X+ voltage
        assert abs(kv - 3.0) < 1e-9

    def test_current_conversion(self, amp):
        amp._on_reading(1.0, FULL_READING)
        _, ma = amp.buffers["AIN5"].latest()     # X+ current
        assert abs(ma - 10.0) < 1e-9

    def test_negative_rail(self, amp):
        amp._on_reading(1.0, FULL_READING)
        _, kv = amp.buffers["AIN6"].latest()     # X- voltage
        assert abs(kv - (-3.0)) < 1e-9

    def test_starts_live(self, amp):
        assert amp._is_live is True
        assert amp.slider.value() == 10_000

    def test_slider_enters_frozen(self, amp):
        for i in range(5):
            amp._on_reading(float(i), FULL_READING)
        amp._on_slider_changed(4000)
        assert amp._is_live is False
        assert amp._frozen_right_edge is not None

    def test_jump_to_live(self, amp):
        for i in range(5):
            amp._on_reading(float(i), FULL_READING)
        amp._on_slider_changed(4000)
        amp._jump_to_live()
        assert amp._is_live is True

    def test_redraw_empty_is_safe(self, amp):
        amp._redraw_plot()   # must not raise


class TestCurrentTabIgnoresAmps:
    @pytest.fixture
    def cur(self, qapp):
        from current_tab import CurrentTab
        t = CurrentTab()
        yield t
        t.shutdown()

    def test_buffers_only_log_amp_channels(self, cur):
        assert set(cur.buffers.keys()) == set(SC.LABJACK_CHANNEL_MAP.keys())
        for amp_ain in SC.AMP_AIN_NAMES:
            assert amp_ain not in cur.buffers

    def test_log_amp_math_unchanged_with_twelve_channels(self, cur):
        """The regression that matters: feeding 12 channels instead of 4 must
        not change what the log-amp tab computes. 3.0 V -> 1 µA, as before."""
        cur._on_reading(1.0, FULL_READING)
        _, i = cur.buffers["AIN0"].latest()
        assert abs(i - 1e-6) < 1e-9
        assert "µA" in cur.lbl_i["AIN0"].text()

    def test_centering_still_works(self, cur):
        cur._on_reading(1.0, FULL_READING)
        assert "+0.000" in cur.lbl_xc.text()


class TestBothTabsShareOneReading:
    def test_same_dict_feeds_both_correctly(self, qapp):
        from amp_tab import AmpTab
        from current_tab import CurrentTab
        amp = AmpTab()
        cur = CurrentTab()
        try:
            # Exactly what MainWindow does: fan one reading to both tabs.
            amp._on_reading(1.0, FULL_READING)
            cur._on_reading(1.0, FULL_READING)

            _, kv = amp.buffers["AIN4"].latest()
            assert abs(kv - 3.0) < 1e-9        # amp tab got its channel

            _, i = cur.buffers["AIN0"].latest()
            assert abs(i - 1e-6) < 1e-9        # log-amp tab got its channel
        finally:
            amp.shutdown()
            cur.shutdown()
