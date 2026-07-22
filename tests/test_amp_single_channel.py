"""
GUI-level tests for single-channel streaming in the HV Amplifiers tab.

Verifies that AmpTab reacts to single-channel profiles correctly:
  - the single-channel target selector enables only in single mode,
  - a single streamed channel updates its own readout while the other
    monitors are shown paused,
  - the single plot's waveform snapshot (shown at small time windows)
    follows the streamed target (voltage OR current).

There is intentionally ONE plot: it switches between the 10 Hz trend (wide
windows) and the raw-waveform snapshot (windows below SNAPSHOT_MAX_SECONDS).
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

        tab.on_profile_changed("FULL")
        assert tab._single_mode is False
        assert not tab._single_combo.isEnabled()

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
        # Target a CURRENT monitor and confirm the single-plot waveform snapshot
        # picks it up on the current axis.
        target = SC.AMP_CHANNEL_MAP["Y-"]["current"]   # AIN6
        idx = tab._single_combo.findData(target)
        tab._single_combo.setCurrentIndex(idx)
        tab.on_profile_changed("SINGLE_HIRES")

        tab._on_window(_single_payload("SINGLE_HIRES", target, 0.5))

        assert tab._single_target_ain == target

        # A current target shows only the current axis (voltage axis hidden).
        assert tab.ax_i.get_visible()
        assert not tab.ax_v.get_visible()

        # The raw waveform is stored in mA (0.5 V * 10 mA/V = 5 mA).
        chunks = tab._wave_chunks[target]
        assert len(chunks) > 0
        _, vals = chunks[-1]
        assert vals.max() == pytest.approx(5.0)

        # Zoom into the waveform (snapshot mode) and confirm the target line
        # on the current axis actually receives data.
        tab._window_seconds = 0.05
        tab._is_live = True
        assert tab._is_snapshot()
        tab._redraw_plot()
        assert len(tab._lines_i["Y-"].get_xdata()) > 0

    def test_multichannel_mode_keeps_all_live(self, tab):
        # Regression: FULL profile leaves every monitor un-muted.
        tab.on_profile_changed("FULL")
        for amp in SC.AMP_LABELS:
            assert "#bbb" not in tab.lbl_kv[amp].styleSheet()


class TestApplyAndSnapshot:
    def test_combo_change_stages_without_emitting(self, tab):
        # Changing the profile combo must NOT touch the hardware; it only stages.
        emitted = []
        tab.profile_change_requested.connect(emitted.append)

        idx = tab._profile_combo.findData("WAVEFORM")
        tab._profile_combo.setCurrentIndex(idx)

        assert emitted == []                      # nothing applied yet
        assert tab._apply_btn.isEnabled()         # Apply lit up as pending

    def test_apply_emits_and_clears_pending(self, tab):
        emitted = []
        tab.profile_change_requested.connect(emitted.append)

        idx = tab._profile_combo.findData("WAVEFORM")
        tab._profile_combo.setCurrentIndex(idx)
        tab._apply_stream_settings()

        assert emitted == ["WAVEFORM"]
        assert not tab._apply_btn.isEnabled()     # staged == applied now

    def test_wide_window_is_trend_small_window_is_snapshot(self, tab):
        tab.on_profile_changed("FULL")
        target = SC.AMP_CHANNEL_MAP["X+"]["voltage"]
        tab._on_window(_single_payload("FULL", target, 3.0))

        tab._window_seconds = 120.0
        assert not tab._is_snapshot()
        tab._window_seconds = 0.02
        assert tab._is_snapshot()
