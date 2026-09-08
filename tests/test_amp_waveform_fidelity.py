"""
Waveform-fidelity tests for the HV Amplifiers tab snapshot (scope) view.

Covers the two reported problems:

  1. "Tripping" of the voltage waveform in the 62.5 ms – 1 s snapshot range.
     Snapshot mode stitches ~10 separate 0.1 s stream windows together.  With a
     sample-accurate stream clock and per-sample reconstruction, consecutive
     chunks must join SEAMLESSLY — every sample spacing (including the ones that
     straddle a chunk boundary) equals the sample period, with no overlap or gap.

  2. Triangle-tip "somersaults".  Envelope decimation must keep each bin's two
     extrema at their true sample positions in time order, so a sharp peak is
     drawn the right way round instead of collapsing/flipping onto a vertical
     segment.
"""
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from rbl.config import hardware_config as SC
from tests.payloads import LabJackFeed


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def tab(qapp):
    from rbl.gui.amp_tab import AmpTab
    return AmpTab()


@pytest.fixture
def feed(tab):
    """The tab is fed through a real Beamline: the samples it rings are the
    ones Beamline scaled to kV/mA, not ones the tab scaled itself."""
    return LabJackFeed(tab)


def _wave_payload(ain, wave, t_end, sample_period):
    """A multi-channel FULL-style window carrying one amp channel's waveform."""
    wave = np.asarray(wave, dtype=float)
    channels = {
        ain: {
            "waveform": wave.copy(),
            "peak":  float(np.max(np.abs(wave))),
            "pk_pk": float(wave.max() - wave.min()),
            "rms":   float(np.sqrt(np.mean(wave ** 2))),
        }
    }
    for other in SC.AMP_AIN_NAMES:
        channels.setdefault(other, None)
    return {
        "profile": "FULL",
        "window_samples": len(wave),
        "t": t_end,
        "sample_period": sample_period,
        "channels": channels,
    }


class TestSeamlessStitching:
    def test_consecutive_windows_join_without_seams(self, tab, feed):
        # Three back-to-back 0.1 s windows at 8 kS/s (FULL amp rate).
        target = SC.AMP_CHANNEL_MAP["X+"]["voltage"]   # AIN13
        n  = 800
        dt = 1.0 / 8000.0
        window_dur = n * dt                            # 0.1 s

        for k in range(3):
            t_end = (k + 1) * window_dur               # sample-accurate clock
            wave  = np.arange(n) + k * n               # values are arbitrary here
            feed.send_payload(_wave_payload(target, wave, t_end, dt))

        # The tab adopts the stream's real sample period from the payload.
        assert tab.wave_ring._dt == pytest.approx(dt)

        series = tab.wave_ring.series(target, 0.0, 3 * window_dur)
        assert series is not None
        times, _vals = series

        # All 2400 samples present, strictly increasing in time.
        assert len(times) == 3 * n
        diffs = np.diff(times)
        assert np.all(diffs > 0)
        # Every spacing — INCLUDING the two chunk boundaries — equals one
        # sample period.  A seam (overlap/gap) would show up as a diff != dt.
        assert np.allclose(diffs, dt, rtol=0, atol=dt * 1e-6)

    def test_falls_back_to_nominal_period_without_sample_period(self, tab, feed):
        # Legacy payloads (no sample_period) must still reconstruct a sane,
        # monotonic timeline from the nominal window duration / sample count.
        target = SC.AMP_CHANNEL_MAP["Y+"]["voltage"]   # AIN9
        n = 800
        window_dur = tab.WINDOW_DURATION_S
        for k in range(2):
            payload = _wave_payload(target, np.zeros(n), (k + 1) * window_dur, None)
            payload.pop("sample_period")
            feed.send_payload(payload)

        series = tab.wave_ring.series(target, 0.0, 2 * window_dur)
        assert series is not None
        times, _ = series
        assert np.all(np.diff(times) > 0)

# Decimation (min/max envelope preservation) is tested directly against
# rbl.hardware.waveform_ring.decimate_minmax in tests/test_waveform_ring.py —
# no Qt/AmpTab needed for pure-function coverage of that algorithm.
