"""
amp_trace.py
Turn a run of raw HV voltage-monitor windows into the short, cycle-aligned
trace the Overview draws each amplifier pair on.

Moved out of rbl/state/beamline.py, where it was roughly a fifth of the file
and the only part with no instrument, no Qt signal, and no lifecycle in it.
Everything here is a function of the samples pushed in so far, which is what
makes it testable on its own — feed it windows, read the traces back — and
what makes it belong next to waveform_ring.py and waveform_period.py rather
than beside the driver ownership it used to sit in.

WHAT IT IS FOR
--------------
The question the Overview's pair trace exists to answer is "are these two
plates mirror images of each other?". Answering it needs three things that
the per-window scalars (peak / pk-pk / RMS) cannot give:

  1. a whole number of cycles, so the picture does not change shape with the
     drive frequency (see waveform_period.py — this is the aliasing fix);
  2. the SAME window for both plates of a pair, so the 0/180 relationship
     survives; choosing a window per channel aligns each plate to its own
     zero crossing and deletes the very thing being checked;
  3. a shared sample grid after decimation, so a phase difference on screen
     is a real one and not a resampling artefact.

All three are properties of a PAIR over TIME, not of one channel in one
window, which is why this holds history of its own instead of being a
function of the latest payload.
"""
import math

from rbl.config import hardware_config as SC
from rbl.config.labjack_stream_config import GUI_REFRESH_HZ
from rbl.hardware.waveform_period import cycle_slice, estimate_period_samples
from rbl.hardware.waveform_ring import AlignedWaveHistory


class AmpTraceBuilder:
    """Rolling HV voltage-monitor history -> per-amplifier cycle traces.

    Push one stream window's channel dict per window; call `traces()` with the
    same payload to get {amp label: (wave_kv, span_s, freq_hz)}.
    """

    # Points kept per channel per window for the Overview's overlaid pair
    # trace. Enough to read a triangle's shape at a glance; small enough that
    # four of them crossing a Qt signal at 10 Hz costs nothing.
    WAVE_POINTS = 120

    # Cycles of the measured drive to put in that trace. Two, not one: one
    # cycle drawn edge to edge gives nothing to compare its start against, and
    # a pair whose members repeat at slightly different rates only separates
    # visibly over more than a single period.
    TARGET_CYCLES = 2

    # Cycles the period is measured over, when that many are on hand. Well
    # above the two the estimator needs, because accuracy near its floor is
    # poor (a 20 Hz drive measured from a single 0.1 s window — two cycles —
    # came out 5% high) and the caption quotes this number as a frequency.
    RATE_CYCLES = 8

    # Raw samples kept per voltage channel. The bound is samples rather than
    # seconds because that is what costs memory and FFT time; what it buys in
    # seconds depends on the profile (~4 s on FULL, ~0.33 s on SINGLE_FAST),
    # and that in turn sets the slowest drive whose period can be measured —
    # roughly 0.5 Hz on FULL. Slower than that, the trace falls back to one
    # raw window and says so rather than pretending to have found a cycle.
    HISTORY_SAMPLES = 32768

    def __init__(self, history_samples: int = None):
        self.history = AlignedWaveHistory(history_samples or self.HISTORY_SAMPLES)

    def clear(self):
        """Drop all history.

        Called whenever the stream restarts: a new profile means a new sample
        rate, and stitching across that seam would put two different time
        bases in one trace — and measure a period that never existed.
        """
        self.history.clear()

    def push(self, channels: dict):
        """File one window's raw HV voltage monitors into the history."""
        window = {}
        for amp in SC.AMP_LABELS:
            ch = channels.get(SC.AMP_CHANNEL_MAP[amp]["voltage"])
            wave = ch.get("waveform") if ch is not None else None
            if wave is not None and len(wave):
                window[SC.AMP_CHANNEL_MAP[amp]["voltage"]] = wave
        self.history.push(window)

    def traces(self, payload: dict) -> dict:
        """Per amplifier: (wave_kv, span_s, freq_hz) for the Overview trace."""
        dt = payload.get("sample_period")
        if not dt:
            # Pure-math callers (tests, the self-test) omit it; the nominal
            # window duration over its sample count is the same number to
            # within the device's rate rounding.
            n = payload.get("window_samples") or 0
            dt = (1.0 / GUI_REFRESH_HZ) / n if n else float("nan")

        out = {}
        for axis in ("X", "Y"):
            for amps in self._groups(axis):
                ains = [SC.AMP_CHANNEL_MAP[a]["voltage"] for a in amps]
                available = self.history.aligned_length(ains)
                latest = payload.get("window_samples") or available
                seg, ref, period = self._measure(ains, latest, available)
                if not seg:
                    continue

                bounds = cycle_slice(seg[ref], period, self.TARGET_CYCLES)
                if bounds is None:
                    # No cycle to lock to (flat, noise, or slower than the
                    # history): show the newest raw window, which is what this
                    # trace always showed before it could measure anything.
                    start = max(0, len(seg[ref]) - latest)
                    stop = len(seg[ref])
                    freq_hz = float("nan")
                else:
                    start, stop = bounds
                    freq_hz = 1.0 / (period * dt) if dt else float("nan")

                span_s = (stop - start) * dt
                for amp, ain in zip(amps, ains):
                    out[amp] = (self.decimate(seg[ain][start:stop]),
                                span_s, freq_hz)
        return out

    # ---- Internals -----------------------------------------------------------

    def _measure(self, ains: list, latest: int, available: int) -> tuple:
        """(samples, reference channel, period in samples) for one trace group.

        Starts from the newest window alone — at any raster rate above a few Hz
        the cycles are already in there, and keeping the transform small is
        what lets this run on every frame — and reaches back into the history
        only when that record is too short to measure a period well from.
        """
        seg = self.history.aligned_tail(ains, min(latest, available))
        if not seg:
            return {}, None, float("nan")
        # The harder-driven plate is the cleaner trigger: a plate sitting near
        # zero is mostly monitor noise, and triggering on noise moves BOTH
        # traces, since the pair shares the window by design.
        ref = max(ains, key=lambda a: float(seg[a].max() - seg[a].min()))

        period = estimate_period_samples(seg[ref])
        # A period measured from a record barely longer than itself is a poor
        # one — the autocorrelation has only a few samples of overlap left at
        # that lag — and a 5% error on the frequency is the difference between
        # a caption you can trust and one you cannot. Widen the record until it
        # holds a comfortable number of cycles; when nothing repeated inside
        # one window at all, widen it to everything and try again.
        wanted = (available if math.isnan(period)
                  else min(available, int(period * self.RATE_CYCLES) + 2))
        if wanted > len(seg[ref]):
            longer = self.history.aligned_tail(ains, wanted)
            refined = estimate_period_samples(longer[ref])
            if not math.isnan(refined):
                return longer, ref, refined
            if not math.isnan(period):
                # The longer record disagrees (the drive changed inside it, or
                # it spans a settling transient). Keep the estimate that worked
                # and the longer record with it — the extra samples are the
                # room the trigger needs to hold the trace still.
                return longer, ref, period
        return seg, ref, period

    def _groups(self, axis: str) -> list:
        """The amplifiers that share one trace window on *axis*.

        Normally the pair, together: the window is chosen once, from whichever
        plate is driven harder, and applied to both. Choosing it per channel
        would align each plate to its own zero crossing and delete the phase
        relationship the pair trace exists to show.

        A window can only be shared by channels sampled in the SAME stream
        windows, though, and a single-channel profile samples one plate of the
        pair. The survivor then gets a window of its own — half a picture, but
        the trace is what tells you that profile is running.
        """
        pair = [f"{axis}+", f"{axis}-"]
        ains = [SC.AMP_CHANNEL_MAP[a]["voltage"] for a in pair]
        if self.history.aligned_length(ains) >= 2:
            return [pair]
        return [[amp] for amp, ain in zip(pair, ains)
                if self.history.aligned_length([ain]) >= 2]

    @classmethod
    def decimate(cls, wave) -> tuple:
        """Thin a slice of raw samples down to WAVE_POINTS on a UNIFORM grid.

        Uniform striding, deliberately, not the envelope-preserving min/max
        binning the amplifier tab's plot uses: min/max emits each point at its
        own sample position, so two channels decimated that way no longer
        share an x grid — and a pair drawn on two different grids shows a
        phase difference that is pure resampling artefact. Peaks matter on a
        plot you read values off; a shared time base matters here.

        Striding is only safe because the caller hands over a couple of cycles
        rather than a whole window: at ~60 points per cycle the shape survives.
        Striding 200 cycles down to 120 points, which is what this used to be
        given, is aliasing — it drew a beat pattern of the decimation.
        """
        if wave is None or len(wave) == 0:
            return ()
        step = max(1, len(wave) // cls.WAVE_POINTS)
        return tuple(
            float(v) * SC.VOLTAGE_MONITOR_KV_PER_VOLT
            for v in wave[::step][:cls.WAVE_POINTS]
        )
