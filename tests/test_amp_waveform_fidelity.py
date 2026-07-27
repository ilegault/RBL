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

pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtWidgets import QApplication

from rbl.config import hardware_config as SC


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def tab(qapp):
    from rbl.gui.amp_tab import AmpTab
    return AmpTab()


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
    def test_consecutive_windows_join_without_seams(self, tab):
        # Three back-to-back 0.1 s windows at 8 kS/s (FULL amp rate).
        target = SC.AMP_CHANNEL_MAP["X+"]["voltage"]   # AIN13
        n  = 800
        dt = 1.0 / 8000.0
        window_dur = n * dt                            # 0.1 s

        for k in range(3):
            t_end = (k + 1) * window_dur               # sample-accurate clock
            wave  = np.arange(n) + k * n               # values are arbitrary here
            tab._on_window(_wave_payload(target, wave, t_end, dt))

        # The tab adopts the stream's real sample period from the payload.
        assert tab._wave_dt == pytest.approx(dt)

        series = tab._snapshot_series(target, 0.0, 3 * window_dur)
        assert series is not None
        times, _vals = series

        # All 2400 samples present, strictly increasing in time.
        assert len(times) == 3 * n
        diffs = np.diff(times)
        assert np.all(diffs > 0)
        # Every spacing — INCLUDING the two chunk boundaries — equals one
        # sample period.  A seam (overlap/gap) would show up as a diff != dt.
        assert np.allclose(diffs, dt, rtol=0, atol=dt * 1e-6)

    def test_falls_back_to_nominal_period_without_sample_period(self, tab):
        # Legacy payloads (no sample_period) must still reconstruct a sane,
        # monotonic timeline from the nominal window duration / sample count.
        target = SC.AMP_CHANNEL_MAP["Y+"]["voltage"]   # AIN9
        n = 800
        window_dur = tab.WINDOW_DURATION_S
        for k in range(2):
            payload = _wave_payload(target, np.zeros(n), (k + 1) * window_dur, None)
            payload.pop("sample_period")
            tab._on_window(payload)

        series = tab._snapshot_series(target, 0.0, 2 * window_dur)
        assert series is not None
        times, _ = series
        assert np.all(np.diff(times) > 0)


class TestDecimationPreservesPeaks:
    def test_triangle_apex_not_flipped(self):
        from rbl.gui.amp_tab import AmpTab

        # A single sharp triangle: up to an apex at the centre, then down.
        n = 20_000
        half = n // 2
        y = np.concatenate([np.linspace(-1.0, 1.0, half),
                            np.linspace(1.0, -1.0, n - half)])
        x = np.arange(n, dtype=float)

        xd, yd = AmpTab._decimate_minmax(x, y, 2000)

        # Fewer points, but the envelope (true peak/trough) is preserved.
        assert len(xd) < n
        assert yd.max() == pytest.approx(y.max())
        assert yd.min() == pytest.approx(y.min())

        # x must stay monotonic non-decreasing — the old min-then-max-at-one-x
        # scheme produced backward steps at falling edges (the "somersault").
        assert np.all(np.diff(xd) >= 0)

        # The apex is reproduced near the true peak location, not at a bin's
        # left edge far from it.
        apex_x = xd[np.argmax(yd)]
        assert abs(apex_x - x[np.argmax(y)]) < n / 2000

    def test_descending_ramp_not_reversed(self):
        from rbl.gui.amp_tab import AmpTab

        # Pure descending ramp: max at the very start, min at the very end.
        n = 10_000
        x = np.arange(n, dtype=float)
        y = np.linspace(5.0, -5.0, n)

        xd, yd = AmpTab._decimate_minmax(x, y, 2000)

        assert np.all(np.diff(xd) >= 0)          # x never steps backward
        # Overall trend stays descending (first output well above the last).
        assert yd[0] > yd[-1]
        assert yd.max() == pytest.approx(5.0)
        assert yd.min() == pytest.approx(-5.0)

    def test_short_series_returned_unchanged(self):
        from rbl.gui.amp_tab import AmpTab

        x = np.arange(500, dtype=float)
        y = np.sin(x)
        xd, yd = AmpTab._decimate_minmax(x, y, 2000)
        assert np.array_equal(xd, x)
        assert np.array_equal(yd, y)
