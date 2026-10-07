"""
spike_detector.py
Finds current spikes on one amplifier's current monitor, judged against what
that amplifier normally draws. Pure: numpy only, no Qt, no clock, no hardware.

WHY THIS EXISTS
---------------
An EEL5000 can supply 100 mA for 4 ms but only 20 mA continuously, and what the
operator has to defend to a PI is how close an irradiation came to those limits
over 12 hours. A fixed alarm level cannot do that: a small raster draws a 2 mA
baseline, where a 6 mA excursion is a real event, while a large one draws 15 mA
and sits near any fixed level all the time. So a spike is judged against the
REFERENCE CURRENT - what this amplifier drew during the first minute at this
operating point.

THE THRESHOLD
-------------
    threshold = max(2 x reference, reference + 5 x noise)

Twice the reference is the rule; the noise term only lifts it when the monitor
is noisy enough that twice the reference would sit inside the noise (this rig's
current monitor has about 1.4 mA rms noise). Without it a noisy monitor floods
the log with false spikes; with a larger fixed multiple a quiet monitor misses
real ones. Nothing else sets the threshold - in particular the 20 mA and 100 mA
ratings are reference levels on a chart, not part of the definition.

Noise is 1.4826 x the median absolute deviation of |I|, not the standard
deviation: the reference minute may itself contain a spike, and a standard
deviation would let one spike widen its own threshold.

TWO SAMPLES, NOT ONE
--------------------
White noise exceeds a 5-sigma threshold on about 3 samples in 10 million. At
7.5 kS/s that is roughly eight single-sample false spikes in an hour on a
perfectly healthy amplifier - enough to bury a real event in a 12-hour log. Two
neighbouring samples both above it happens about once in 1e13, so a spike must
be at least MIN_SPIKE_SAMPLES contiguous samples. The price is that a genuine
one-sample (133 us) excursion is not logged; at this sample rate such a spike
has no resolvable peak anyway.

WHAT IS AND IS NOT KNOWN ABOUT A SPIKE
--------------------------------------
Charge above the reference is an integral, so it is right even when the spike is
brief. The PEAK is not: the monitor and the sampling smooth a brief spike down.
A spike spanning fewer than MIN_RESOLVED_SAMPLES samples, or lasting under 100 us,
reports `peak_is_lower_bound = True` - the peak was at least that large, and the
caller must not present it as the true peak. At 7.5 kS/s one sample is already
133 us, so the sample-count rule is what flags brief spikes there; the 100 us
rule only matters at faster sampling.

STATE
-----
Windows arrive back to back and a spike does not respect the boundary. The
detector keeps exactly the state needed to join a spike that is still open at
the end of one `feed` to its continuation in the next, and reports it once,
when it ends. Windows must be contiguous: the caller owns that.
"""
from dataclasses import dataclass

import numpy as np

# A run shorter than this is noise, not a spike (see "TWO SAMPLES, NOT ONE").
MIN_SPIKE_SAMPLES = 2
# A peak is only trustworthy when several samples sit on it.
MIN_RESOLVED_SAMPLES = 4
# Shorter than this the monitor's own response smooths the peak, whatever the
# sample rate.
LOWER_BOUND_DURATION_S = 100e-6

_THRESHOLD_FLOOR_FACTOR = 2.0   # threshold is at least this x the reference
_THRESHOLD_NOISE_WIDTHS = 5.0   # ...and at least this many noise widths above it
_MAD_TO_SIGMA = 1.4826          # median absolute deviation -> Gaussian sigma


@dataclass(frozen=True)
class Reference:
    """What an amplifier normally draws at one operating point (mA)."""
    current_ma: float
    noise_ma: float


@dataclass(frozen=True)
class Spike:
    """One interval during which |I| stayed above the spike threshold."""
    start_s: float
    duration_s: float
    peak_ma: float
    charge_uc: float                  # integral of (|I| - reference), microcoulombs
    peak_is_lower_bound: bool


def capture_reference(samples_ma, dt_s: float) -> Reference:
    """Reference current and noise from (typically one minute of) samples.

    `dt_s` is accepted so every caller states its sample interval alongside the
    data; the statistics themselves do not depend on it.
    """
    a = np.abs(np.asarray(samples_ma, dtype=float))
    a = a[np.isfinite(a)]
    if a.size == 0:
        raise ValueError("capture_reference needs at least one finite sample")
    current = float(np.median(a))
    noise = float(_MAD_TO_SIGMA * np.median(np.abs(a - current)))
    return Reference(current_ma=current, noise_ma=noise)


def spike_threshold(ref: Reference) -> float:
    """max(2 x reference, reference + 5 x noise), in mA."""
    return max(_THRESHOLD_FLOOR_FACTOR * ref.current_ma,
               ref.current_ma + _THRESHOLD_NOISE_WIDTHS * ref.noise_ma)


class SpikeDetector:
    """Feed contiguous windows of one plate's current (mA); get finished spikes."""

    def __init__(self, ref: Reference, dt_s: float):
        self._ref = ref
        self._dt = float(dt_s)
        self._threshold = spike_threshold(ref)
        self._open = False        # a run is in progress at the end of the last feed
        self._start_s = 0.0
        self._n = 0
        self._peak = 0.0
        self._excess_ma = 0.0     # sum of (|I| - reference) over the run

    @property
    def threshold_ma(self) -> float:
        return self._threshold

    def feed(self, samples_ma, t0_s: float) -> list[Spike]:
        """Process one window whose first sample is at `t0_s`.

        A run still above threshold at the end of the window is held back and
        reported by the window in which it ends.
        """
        a = np.abs(np.asarray(samples_ma, dtype=float))
        n = a.size
        if n == 0:
            return []
        over = a > self._threshold           # NaN compares False: never a spike
        d = np.diff(np.concatenate(([self._open], over)).astype(np.int8))
        starts = [int(i) for i in np.flatnonzero(d == 1)]
        ends = [int(i) for i in np.flatnonzero(d == -1)]

        out: list[Spike] = []
        if self._open:
            # The run in progress continues from sample 0 to its end (or beyond
            # this window).
            end = ends.pop(0) if ends else n
            self._extend(a, 0, end)
            if end < n:
                self._close_into(out)
        for s in starts:
            e = ends.pop(0) if ends else n
            self._begin(t0_s + s * self._dt)
            self._extend(a, s, e)
            if e < n:
                self._close_into(out)
        return out

    def _begin(self, start_s: float) -> None:
        self._open = True
        self._start_s = start_s
        self._n = 0
        self._peak = 0.0
        self._excess_ma = 0.0

    def _extend(self, a: np.ndarray, s: int, e: int) -> None:
        seg = a[s:e]
        if seg.size == 0:
            return
        self._n += int(seg.size)
        self._peak = max(self._peak, float(seg.max()))
        self._excess_ma += float(seg.sum() - seg.size * self._ref.current_ma)

    def _close_into(self, out: list[Spike]) -> None:
        spike = self._close()
        if spike is not None:
            out.append(spike)

    def _close(self) -> Spike | None:
        if self._n < MIN_SPIKE_SAMPLES:
            self._open = False
            return None
        duration = self._n * self._dt
        spike = Spike(
            start_s=self._start_s,
            duration_s=duration,
            peak_ma=self._peak,
            charge_uc=self._excess_ma * self._dt * 1e3,      # mA x s = mC
            peak_is_lower_bound=(self._n < MIN_RESOLVED_SAMPLES
                                 or duration < LOWER_BOUND_DURATION_S),
        )
        self._open = False
        return spike
