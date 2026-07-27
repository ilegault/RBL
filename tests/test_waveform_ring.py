"""
Unit tests for rbl.hardware.waveform_ring — no Qt required.

Covers the two problems the min/max decimation was already fixed for once:
triangle-tip "somersaults" and backward x-steps on a falling edge, plus the
basic store/series/clear behaviour of WaveformRing itself.
"""
import numpy as np
import pytest

from rbl.hardware.waveform_ring import WaveformRing, decimate_minmax


class TestDecimationPreservesPeaks:
    def test_triangle_apex_not_flipped(self):
        # A single sharp triangle: up to an apex at the centre, then down.
        n = 20_000
        half = n // 2
        y = np.concatenate([np.linspace(-1.0, 1.0, half),
                            np.linspace(1.0, -1.0, n - half)])
        x = np.arange(n, dtype=float)

        xd, yd = decimate_minmax(x, y, 2000)

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
        # Pure descending ramp: max at the very start, min at the very end.
        n = 10_000
        x = np.arange(n, dtype=float)
        y = np.linspace(5.0, -5.0, n)

        xd, yd = decimate_minmax(x, y, 2000)

        assert np.all(np.diff(xd) >= 0)          # x never steps backward
        # Overall trend stays descending (first output well above the last).
        assert yd[0] > yd[-1]
        assert yd.max() == pytest.approx(5.0)
        assert yd.min() == pytest.approx(-5.0)

    def test_short_series_returned_unchanged(self):
        x = np.arange(500, dtype=float)
        y = np.sin(x)
        xd, yd = decimate_minmax(x, y, 2000)
        assert np.array_equal(xd, x)
        assert np.array_equal(yd, y)


class TestWaveformRing:
    def test_empty_ring_has_no_latest(self):
        ring = WaveformRing(["A"], keep_seconds=1.0, window_duration_s=0.1)
        assert ring.latest_t() is None
        assert ring.series("A", 0.0, 1.0) is None

    def test_store_and_series_round_trip(self):
        ring = WaveformRing(["A"], keep_seconds=1.0, window_duration_s=0.1)
        ring.set_sample_period(0.1 / 4)
        ring.store("A", 0.1, np.array([1.0, 2.0, 3.0, 4.0]))
        assert ring.latest_t() == pytest.approx(0.1)
        times, values = ring.series("A", 0.0, 0.1)
        assert len(times) == 4
        assert list(values) == [1.0, 2.0, 3.0, 4.0]

    def test_old_chunks_are_dropped(self):
        ring = WaveformRing(["A"], keep_seconds=0.2, window_duration_s=0.1)
        ring.set_sample_period(0.1 / 4)
        for k in range(5):
            ring.store("A", (k + 1) * 0.1, np.array([float(k)] * 4))
        # Only chunks within keep_seconds + window_duration_s of the newest
        # survive; the earliest ones must have been dropped.
        t_arr, _ = ring.series("A", 0.0, 0.5)
        assert t_arr[0] > 0.1

    def test_clear_empties_all_channels(self):
        ring = WaveformRing(["A", "B"], keep_seconds=1.0, window_duration_s=0.1)
        ring.store("A", 0.1, np.array([1.0]))
        ring.store("B", 0.1, np.array([2.0]))
        ring.clear()
        assert ring.latest_t() is None
