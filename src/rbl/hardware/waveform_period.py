"""
waveform_period.py
Find the repeat period of a sampled deflection waveform, and pick the slice of
a record that shows a whole number of cycles of it, starting at the same point
in the cycle every frame.

Why this exists
---------------
The Overview's HV pair trace drew one whole stream window — 0.1 s, whatever
that happened to contain. That window is fixed and the raster drive is not, so
the picture it produced depended entirely on the drive frequency:

    2 kHz drive : 200 cycles in the window, thinned to 120 points. Uniform
                  striding at less than one point per cycle is aliasing, and
                  what it draws is a beat pattern of the sampling — a shape
                  the amplifier never produced.
    3 Hz drive  : less than a third of one cycle. A slow arc, going one way,
                  with no crossing in it. Nothing to compare.

Either way the question the panel exists for — are these two plates mirror
images? — is unanswerable from the picture. The fix is to stop showing "a
window" and start showing "a cycle": measure the period, then draw a couple of
them.

The three pieces here
---------------------
`estimate_period_samples` measures the period of a record by autocorrelation,
which cares only about repetition and so works on a sine, a triangle, a square
or a clipped mess of any of them without being told which it is.

`cycle_span_samples` turns a period into how many samples to draw.

`cycle_slice` places that span in the record — starting on a rising crossing of
the mean, which is what a scope's trigger does and for the same reason. Windows
arrive at 10 Hz with no relationship to the drive phase, so a slice taken from
the end of the record starts at a random phase and the trace jumps every frame.
Triggered, the wave stands still and a change on it is a real change.

Pure math on sequences of floats. No hardware, no Qt.
"""
import math

import numpy as np

# An autocorrelation peak below this is drift or noise finding itself, not a
# waveform repeating. Deliberately low: a real drive monitored through a 1000:1
# divider correlates near 1.0, so anything in between is a signal we would
# rather not claim to have measured.
MIN_CORRELATION = 0.3

# A record must hold at least this many cycles before its period is believed.
# Autocorrelation at lag L only has (N - L) samples of overlap to work with, so
# a "period" measured from a record barely longer than itself is mostly an
# artefact of the record ending.
MIN_CYCLES_FOR_ESTIMATE = 2

# Below this RMS (volts at the monitor BNC, so 1 mV == 1 V at the plate) the
# channel is a dead input or an undriven plate. Noise has no period; reporting
# one would put a confident frequency under a flat line.
DEFAULT_MIN_RMS = 1e-3


def estimate_period_samples(wave, min_rms: float = DEFAULT_MIN_RMS) -> float:
    """Repeat period of *wave*, in samples, or NaN if it has none.

    NaN means "this record does not show a repeating waveform" — which covers
    a flat channel, a noisy one, and (importantly, because it is the case that
    made the old trace useless at low frequencies) a drive slower than the
    record is long. All three want the same fallback from the caller, and none
    of them should produce a number.

    Autocorrelation rather than an FFT peak or zero-crossing counting: the
    deflection drive is a triangle as often as a sine, so its spectrum has
    harmonics that can outrank the fundamental, and its monitored crossings sit
    in enough noise to count double. Repetition is the property being measured,
    so measure that directly.
    """
    x = np.asarray(wave, dtype=float)
    n = x.size
    if n < 16 or not np.all(np.isfinite(x)):
        return float("nan")

    x = x - x.mean()
    if math.sqrt(float(np.mean(x * x))) < min_rms:
        return float("nan")

    # Zero-padded to at least 2N so the inverse transform is the LINEAR
    # autocorrelation. Without the padding it wraps, and the wrap-around term
    # is itself periodic in N — it would invent a period on a record that has
    # none.
    size = 1 << int(2 * n - 1).bit_length()
    spec = np.fft.rfft(x, size)
    ac = np.fft.irfft(spec * np.conj(spec), size)[:n]
    ac /= ac[0]

    max_lag = n // MIN_CYCLES_FOR_ESTIMATE
    # Everything up to the first negative correlation is the central lobe —
    # lag 0 is always the highest peak, and a waveform is always similar to
    # itself slightly shifted. The fundamental is the first peak AFTER the
    # signal has stopped resembling itself at all.
    negative = np.flatnonzero(ac[:max_lag] < 0.0)
    if negative.size == 0:
        return float("nan")
    first = int(negative[0])
    if first >= max_lag:
        return float("nan")

    lag = first + int(np.argmax(ac[first:max_lag]))
    if ac[lag] < MIN_CORRELATION:
        return float("nan")
    return _interpolate_peak(ac, lag)


def _interpolate_peak(ac, lag: int) -> float:
    """Sub-sample peak position by fitting a parabola to its three points.

    The period is rarely a whole number of samples, and rounding it to one puts
    a fixed error into every cycle of the displayed span — at 20 cycles a
    half-sample error is ten samples of drift across the trace.
    """
    if lag <= 0 or lag + 1 >= ac.size:
        return float(lag)
    y0, y1, y2 = float(ac[lag - 1]), float(ac[lag]), float(ac[lag + 1])
    denom = y0 - 2.0 * y1 + y2
    if denom == 0.0:
        return float(lag)
    offset = 0.5 * (y0 - y2) / denom
    if not -0.5 <= offset <= 0.5:
        return float(lag)          # not a peak; trust the sample index
    return float(lag) + offset


def cycle_span_samples(period_samples: float, available: int,
                       target_cycles: int = 2) -> int:
    """How many samples to draw: *target_cycles* periods, or as many as fit.

    Never fewer than one whole cycle while one is available — a fraction of a
    cycle is the picture this module exists to stop drawing. Returns 0 when
    even one does not fit, which tells the caller to fall back.

    A whole period is left spare on top of the span wherever possible, because
    that spare is where the trigger looks for its edge. Without it the span is
    chosen first and the trigger takes what room is left, which at the slow end
    (a record holding 2.8 cycles) is sometimes under a period and holds no
    edge at all — so the window free-ran on some frames and locked on others,
    and the trace flicked between the two. Better to draw one steady cycle.
    """
    if not math.isfinite(period_samples) or period_samples <= 0.0:
        return 0
    counts = range(max(1, int(target_cycles)), 0, -1)
    for cycles in counts:
        span = int(round(period_samples * cycles))
        if span >= 2 and span + period_samples <= available:
            return span
    # Nothing leaves room for a trigger. Whole cycles still beat a fraction of
    # one, so take the widest that fits and let the caller free-run it.
    for cycles in counts:
        span = int(round(period_samples * cycles))
        if 2 <= span <= available:
            return span
    return 0


def cycle_slice(reference, period_samples: float,
                target_cycles: int = 2) -> tuple | None:
    """(start, stop) into *reference* for a whole-cycle, phase-aligned window.

    The window sits as late in the record as a trigger allows, so the trace
    stays live. Returns None when no whole cycle is available; the caller then
    shows the raw record, which is at least honest about what it has.

    Apply the SAME indices to every channel of a pair. The slice is chosen from
    one reference channel precisely so that both members keep a common time
    base — re-triggering each channel on itself would align them to their own
    zero crossings and erase the phase difference the panel is looking for.
    """
    x = np.asarray(reference, dtype=float)
    span = cycle_span_samples(period_samples, x.size, target_cycles)
    if span == 0:
        return None

    # Drop a cycle at a time until the window is short enough to leave a
    # trigger point in front of it. A record holding 2.8 cycles has only 0.8
    # of a cycle spare once two are drawn, and a crossing need not fall in
    # there — the trace then free-runs, which is the jitter this whole trigger
    # exists to remove. One steady cycle beats two that jump every frame.
    cycles = max(1, int(round(span / period_samples)))
    for count in range(cycles, 0, -1):
        width = int(round(period_samples * count))
        if width < 2:
            break
        start = _trigger_index(x, x.size - width, period_samples)
        if start is not None:
            return start, start + width
    # Nothing to trigger on anywhere (a record barely longer than one cycle).
    # Whole cycles, ending on the newest sample, is still the right picture.
    return x.size - span, x.size


def _trigger_index(x, latest_start: int, period_samples: float):
    """Latest rising crossing of the mean at or before *latest_start*, or None.

    The whole record up to that point is searched, not just the last period.
    Usually the last crossing IS in the last period and the extra scan finds
    nothing new; when it is not, an older crossing shows the same cycle a
    fraction of a second late — which nobody can see — and it holds still,
    which is the entire point.
    """
    if latest_start <= 0:
        return None
    seg = x[:latest_start + 1]
    if seg.size < 2:
        return None

    level = float(x.mean())
    # Index of the sample BEFORE each upward crossing; the window starts on the
    # first sample at or above the mean, i.e. one later.
    rising = np.flatnonzero((seg[:-1] < level) & (seg[1:] >= level))
    if rising.size == 0:
        return None

    # Noise on a slow-moving part of the waveform crosses the mean several
    # times in a row; a real rising edge was still clearly BELOW the mean an
    # eighth of a cycle earlier. Without this the trigger picks whichever
    # noise crossing came last and the trace shivers by a few samples a frame.
    back = max(1, int(period_samples // 8))
    settled = seg[np.maximum(rising - back, 0)] < level
    if settled.any():
        rising = rising[settled]
    return int(rising[-1]) + 1


# --- Self-test ---------------------------------------------------------------

if __name__ == "__main__":
    def _sine(n, period, phase=0.0):
        t = np.arange(n)
        return np.sin(2 * np.pi * (t / period + phase))

    def _triangle(n, period, phase=0.0):
        f = ((np.arange(n) / period) + phase) % 1.0
        return np.where(f < 0.5, 4 * f - 1, 3 - 4 * f)

    # Period recovery, three shapes, exact and awkward periods.
    for shape in (_sine, _triangle):
        for period in (37.0, 64.0, 101.7):
            est = estimate_period_samples(shape(2048, period))
            assert abs(est - period) < 0.05 * period, (shape, period, est)
    square = np.sign(_sine(2048, 80.0))
    assert abs(estimate_period_samples(square) - 80.0) < 2.0

    # Things that have no period must say so, not guess one.
    assert math.isnan(estimate_period_samples(np.zeros(1024)))
    assert math.isnan(estimate_period_samples(np.full(1024, 3.3)))
    rng = np.random.default_rng(0)
    assert math.isnan(estimate_period_samples(rng.normal(0, 1.0, 4096)))
    assert math.isnan(estimate_period_samples(_sine(64, 200.0)))   # < 1 cycle
    assert math.isnan(estimate_period_samples(_sine(300, 200.0)))  # < 2 cycles
    assert math.isnan(estimate_period_samples([1.0, 2.0, 3.0]))    # too short
    assert math.isnan(estimate_period_samples(_sine(1024, 50.0) * 1e-6))  # flat

    # Span: two cycles when they fit, one when only one does, none below that.
    assert cycle_span_samples(50.0, 1000) == 100
    assert cycle_span_samples(50.0, 80) == 50
    assert cycle_span_samples(50.0, 40) == 0
    assert cycle_span_samples(float("nan"), 1000) == 0
    # 2.8 cycles available: two would leave under a period for the trigger, so
    # one steady cycle it is. 3.0 cycles is enough for two.
    assert cycle_span_samples(50.0, 140) == 50
    assert cycle_span_samples(50.0, 150) == 100

    # Trigger: the window starts on a rising crossing, whatever the phase, and
    # the same cycle is framed the same way from a record cut anywhere.
    period = 64.0
    for phase in (0.0, 0.13, 0.5, 0.77):
        wave = _sine(1024, period, phase)
        start, stop = cycle_slice(wave, period)  # type: ignore[misc]
        assert stop - start == 128
        assert start > 0 and wave[start] >= 0.0 > wave[start - 1]
        assert abs(wave[start]) < 0.15, (phase, wave[start])

    # Both members of a pair keep the reference's grid — that is the point.
    a = _triangle(1024, 64.0)
    b = -a
    start, stop = cycle_slice(a, estimate_period_samples(a))  # type: ignore[misc]
    assert np.allclose(a[start:stop], -b[start:stop])

    assert cycle_slice(_sine(64, 200.0), float("nan")) is None

    print("[OK] waveform_period self-test passed")
    print(f"    1 kHz @ 8 kS/s -> period "
          f"{estimate_period_samples(_triangle(800, 8.0)):.2f} samples")
