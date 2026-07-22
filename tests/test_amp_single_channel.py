"""
GUI-level tests for single-channel streaming in the HV Amplifiers tab.

Verifies that AmpTab reacts to single-channel profiles correctly:
  - the single-channel target selector enables only in single mode,
  - a single streamed channel updates its own readout while the other
    monitors are shown paused,
  - the waveform view follows the streamed target (voltage OR current).
"""
import os

import numpy as np
import pytest

# Headless Qt; must be set before importing QtWidgets.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtWidgets import QApplication

from rbl.config import hardware_config as SC
from rbl.config.labjack_stream_config import window_samples


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def tab(qapp):
    from amp_tab import AmpTab
    return AmpTab()


def _single_payload(profile, target_ain, value):
    """Build a stream window with only *target_ain* live (others paused)."""
    win = window_samples(profile)
    wave = np.full(win, value)
    channels = {
        target_ain: {
            "waveform": wave.copy(),
            "peak":  float(np.max(np.abs(wave))),
            "pk_pk": float(wave.max() - wave.min()),
            "rms":   float(np.sqrt(np.mean(wave ** 2))),
        }
    }
    for ain in SC.AMP_AIN_NAMES:
        channels.setdefault(ain, None)
    return {"profile": profile, "window_samples": win, "t": 1.0,
            "channels": channels}


class TestSingleChannelMode:
    def test_target_selector_enables_in_single_mode(self, tab):
        tab.on_profile_changed("SINGLE_FAST")
        assert tab._single_mode is True
        assert tab._single_combo.isEnabled()
        assert not tab._wf_combo.isEnabled()

        tab.on_profile_changed("FULL")
        assert tab._single_mode is False
        assert not tab._single_combo.isEnabled()
        assert tab._wf_combo.isEnabled()

    def test_streamed_channel_updates_and_others_paused(self, tab):
        # Target the X- voltage monitor (AIN11).
        target = SC.AMP_CHANNEL_MAP["X-"]["voltage"]   # AIN11
        idx = tab._single_combo.findData(target)
        tab._single_combo.setCurrentIndex(idx)
        tab.on_profile_changed("SINGLE_FAST")

        tab._on_window(_single_payload("SINGLE_FAST", target, 2.0))

        # Target amp voltage readout is live (2 kV) and not the muted color.
        assert "2.000 kV" in tab.lbl_kv["X-"].text()
        assert "#bbb" not in tab.lbl_kv["X-"].styleSheet()

        # A different amplifier's readout is muted (paused).
        assert "#bbb" in tab.lbl_kv["X+"].styleSheet()

    def test_current_target_waveform_follows_selection(self, tab):
        # Target a CURRENT monitor and confirm the waveform view picks it up.
        target = SC.AMP_CHANNEL_MAP["Y-"]["current"]   # AIN6
        idx = tab._single_combo.findData(target)
        tab._single_combo.setCurrentIndex(idx)
        tab.on_profile_changed("SINGLE_HIRES")

        tab._on_window(_single_payload("SINGLE_HIRES", target, 0.5))
        tab._refresh_waveform_plot()

        # Current monitor (0.5 V * 10 mA/V = 5 mA) shown in mA on the y-axis.
        assert tab._wf_ax.get_ylabel() == "Current (mA)"
        assert "mA" in tab._wf_stats.text()
        assert target in tab._wf_stats.text()

    def test_multichannel_mode_keeps_all_live(self, tab):
        # Regression: FULL profile leaves every monitor un-muted.
        tab.on_profile_changed("FULL")
        for amp in SC.AMP_LABELS:
            assert "#bbb" not in tab.lbl_kv[amp].styleSheet()
