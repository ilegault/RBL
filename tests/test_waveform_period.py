"""
Unit tests for rbl.hardware.waveform_period and the whole-cycle trace window
it drives in Beamline — no Qt, no hardware.

The behaviour under test is what the Overview's HV pair trace shows. It used
to draw one 0.1 s stream window regardless of the drive, which at a kHz raster
is 100+ cycles aliased down to 120 points and at a few Hz is a fraction of one
cycle. Both are pictures you cannot read a push-pull relationship off, so the
window now follows the measured waveform:

  * the period is measured from the samples, not taken from the setpoints —
    the whole point of the HV panel is to show what the amplifiers are doing,
    not what they were told to do;
  * the window holds a whole number of cycles and is triggered on a rising
    edge, so the trace holds still between frames instead of starting wherever
    the stream window happened to begin;
  * a pair shares one window, chosen once, or the phase relationship it exists
    to show would be an artefact of two independent triggers.
"""
import math

import numpy as np
import pytest

from rbl.config import hardware_config as SC
from rbl.hardware.amp_trace import AmpTraceBuilder
from rbl.hardware.waveform_period import (
    cycle_slice,
    cycle_span_samples,
    estimate_period_samples,
)
from rbl.hardware.waveform_ring import AlignedWaveHistory
from rbl.state.beamline import Beamline

RATE_HZ = 8000.0            # FULL profile, per channel
WINDOW = 800                # one GUI window at 10 Hz
DT = 1.0 / RATE_HZ


def sine(n, period, phase=0.0, amp=1.0):
    return amp * np.sin(2 * np.pi * ((np.arange(n) / period) + phase))


def triangle(t, freq_hz, peak=2.0):
    """The deflection drive's actual shape, sampled at times *t* (seconds)."""
    f = (t * freq_hz) % 1.0
    return peak * np.where(f < 0.5, 4 * f - 1, 3 - 4 * f)


class TestPeriodEstimate:
    @pytest.mark.parametrize("period", [23.0, 64.0, 101.7])
    def test_recovers_a_sine_period(self, period):
        assert estimate_period_samples(sine(2048, period)) == pytest.approx(
            period, rel=0.02)

    @pytest.mark.parametrize("period", [37.0, 80.0, 133.3])
    def test_recovers_a_triangle_period(self, period):
        """The drive is a triangle, whose harmonics can outweigh its
        fundamental in a spectrum — repetition is measured directly instead."""
        wave = triangle(np.arange(4096) * DT, RATE_HZ / period)
        assert estimate_period_samples(wave) == pytest.approx(period, rel=0.02)

    def test_recovers_a_square_period(self):
        assert estimate_period_samples(np.sign(sine(2048, 80.0))) == pytest.approx(
            80.0, rel=0.02)

    def test_survives_noise_on_the_monitor(self):
        rng = np.random.default_rng(7)
        wave = sine(2048, 64.0) + rng.normal(0.0, 0.15, 2048)
        assert estimate_period_samples(wave) == pytest.approx(64.0, rel=0.02)

    def test_flat_channel_has_no_period(self):
        """An undriven plate must not be given a frequency: a number under a
        flat line reads as a measurement."""
        assert math.isnan(estimate_period_samples(np.zeros(1024)))
        assert math.isnan(estimate_period_samples(np.full(1024, 3.3)))

    def test_noise_alone_has_no_period(self):
        rng = np.random.default_rng(0)
        assert math.isnan(estimate_period_samples(rng.normal(0, 1.0, 4096)))

    def test_under_two_cycles_is_refused(self):
        """A period measured from a record barely longer than itself is mostly
        an artefact of the record ending. Say nothing instead."""
        assert math.isnan(estimate_period_samples(sine(300, 200.0)))

    def test_a_drive_far_below_the_noise_floor_is_refused(self):
        assert math.isnan(estimate_period_samples(sine(1024, 50.0, amp=1e-6)))


class TestCycleWindow:
    def test_two_cycles_when_the_record_is_long(self):
        assert cycle_span_samples(50.0, 1000) == 100

    def test_falls_back_to_one_cycle(self):
        """2.8 cycles on hand: two would leave under a period for the trigger
        to find an edge in, and a window that free-runs on some frames and
        locks on others makes the trace flick between two scales."""
        assert cycle_span_samples(50.0, 140) == 50
        assert cycle_span_samples(50.0, 150) == 100

    def test_no_whole_cycle_available(self):
        assert cycle_span_samples(50.0, 40) == 0
        assert cycle_slice(sine(40, 50.0), 50.0) is None

    def test_no_period_means_no_window(self):
        assert cycle_slice(sine(400, 50.0), float("nan")) is None

    @pytest.mark.parametrize("phase", [0.0, 0.13, 0.5, 0.77])
    def test_window_starts_on_a_rising_edge_whatever_the_phase(self, phase):
        """Stream windows arrive at 10 Hz with no relation to the drive phase.
        Untriggered, the trace restarts at a random point every frame."""
        wave = sine(1024, 64.0, phase)
        start, stop = cycle_slice(wave, 64.0)
        assert stop - start == 128
        assert wave[start - 1] < 0.0 <= wave[start]

    def test_a_pair_keeps_one_grid(self):
        """The slice is chosen from one reference channel and applied to both;
        re-triggering each channel on itself would align them to their own
        crossings and erase the phase difference being looked for."""
        a = triangle(np.arange(1024) * DT, RATE_HZ / 64.0)
        b = -a
        start, stop = cycle_slice(a, estimate_period_samples(a))
        assert np.allclose(a[start:stop], -b[start:stop])


class TestAlignedWaveHistory:
    def test_tail_spans_several_windows(self):
        hist = AlignedWaveHistory(max_samples=1000)
        for k in range(5):
            hist.push({"A": np.arange(k * 10, k * 10 + 10, dtype=float)})
        assert hist.aligned_length(["A"]) == 50
        assert np.array_equal(hist.aligned_tail(["A"], 15)["A"],
                              np.arange(35, 50, dtype=float))

    def test_channels_stay_index_aligned(self):
        hist = AlignedWaveHistory(max_samples=1000)
        for k in range(4):
            base = np.arange(k * 10, k * 10 + 10, dtype=float)
            hist.push({"A": base, "B": -base})
        tail = hist.aligned_tail(["A", "B"], 25)
        assert np.array_equal(tail["A"], -tail["B"])

    def test_a_missing_channel_breaks_the_run(self):
        """A paused channel must truncate the history, not be spliced over —
        stitching across the gap would put a discontinuity mid-waveform."""
        hist = AlignedWaveHistory(max_samples=1000)
        hist.push({"A": np.zeros(10), "B": np.zeros(10)})
        hist.push({"A": np.zeros(10)})                     # B paused
        hist.push({"A": np.zeros(10), "B": np.zeros(10)})
        assert hist.aligned_length(["A", "B"]) == 10
        assert hist.aligned_length(["A"]) == 30

    def test_old_windows_are_dropped(self):
        hist = AlignedWaveHistory(max_samples=50)
        for _ in range(20):
            hist.push({"A": np.zeros(10)})
        assert hist.aligned_length(["A"]) <= 60

    def test_empty_windows_do_not_pile_up(self):
        """A profile that never samples these channels emits an empty window
        ten times a second, forever. One break marker is enough."""
        hist = AlignedWaveHistory(max_samples=1000)
        for _ in range(5000):
            hist.push({})
        assert len(hist._windows) == 1

    def test_clear_drops_everything(self):
        hist = AlignedWaveHistory(max_samples=100)
        hist.push({"A": np.zeros(10)})
        hist.clear()
        assert hist.aligned_length(["A"]) == 0
        assert hist.aligned_tail(["A"], 5) == {}


def _payload(k: int, freq_hz: float, peak_v: float = 2.0, n: int = WINDOW):
    """One FULL-profile stream window of a push-pull triangle at *freq_hz*.

    Window k continues window k-1 without a seam, exactly as the T7's stream
    does, so the history stitches into a continuous record.
    """
    t = (np.arange(n) + k * n) * DT
    channels = {}
    for amp in SC.AMP_LABELS:
        wave = triangle(t, freq_hz, peak_v)
        if amp.endswith("-"):
            wave = -wave                     # the other plate of the pair
        channels[SC.AMP_CHANNEL_MAP[amp]["voltage"]] = {
            "waveform": wave,
            "peak":  float(np.max(np.abs(wave))),
            "pk_pk": float(wave.max() - wave.min()),
            "rms":   float(np.sqrt(np.mean(wave ** 2))),
        }
        channels[SC.AMP_CHANNEL_MAP[amp]["current"]] = {"rms": 0.2}
    for ain in SC.LABJACK_CHANNEL_MAP:
        channels[ain] = {"mean": 3.0}
    return {"channels": channels, "t": (k + 1) * n * DT,
            "sample_period": DT, "window_samples": n, "profile": "FULL"}


def _run(beamline, freq_hz, frames=40, peak_v=2.0):
    """Feed *frames* consecutive windows; return the last AmpState."""
    seen = []
    beamline.amps_changed.connect(seen.append)
    for k in range(frames):
        beamline.ingest_labjack_window(_payload(k, freq_hz, peak_v))
    return seen[-1]


class TestBeamlineCycleTraces:
    @pytest.fixture
    def beamline(self):
        return Beamline()

    @pytest.mark.parametrize("freq_hz", [1000.0, 137.0, 20.0, 3.0])
    def test_trace_holds_at_least_one_whole_cycle(self, beamline, freq_hz):
        """The whole point: the window follows the drive, from a kHz raster
        (a 2 ms window) down to a few Hz (most of a second)."""
        ch = _run(beamline, freq_hz).channels["X+"]
        assert ch.wave_freq_hz == pytest.approx(freq_hz, rel=0.05)
        assert ch.wave_span_s * freq_hz >= 0.99
        assert ch.wave_span_s * ch.wave_freq_hz == pytest.approx(2.0, abs=0.05)

    def test_a_slow_drive_reaches_back_past_one_stream_window(self, beamline):
        """3 Hz is a third of a cycle per 0.1 s window — a whole cycle only
        exists across several of them."""
        ch = _run(beamline, 3.0).channels["X+"]
        assert ch.wave_span_s > 0.1
        assert len(ch.wave_kv) > 2

    def test_a_fast_drive_zooms_in_instead_of_aliasing(self, beamline):
        """1 kHz put 100 cycles in the old fixed window and strided them down
        to 120 points, which draws a beat pattern of the decimation."""
        ch = _run(beamline, 1000.0).channels["X+"]
        assert ch.wave_span_s < 0.01
        assert len(ch.wave_kv) <= AmpTraceBuilder.WAVE_POINTS

    def test_the_pair_shares_one_window(self, beamline):
        state = _run(beamline, 137.0)
        plus, minus = state.channels["X+"], state.channels["X-"]
        assert len(plus.wave_kv) == len(minus.wave_kv)
        assert plus.wave_span_s == minus.wave_span_s
        assert plus.wave_freq_hz == minus.wave_freq_hz
        # Push-pull: same grid, opposite sign, sample for sample.
        assert np.allclose(plus.wave_kv, [-v for v in minus.wave_kv], atol=1e-9)

    def test_the_trace_holds_still_between_frames(self, beamline):
        """Untriggered, consecutive windows start at unrelated phases and the
        trace jumps every 100 ms."""
        seen = []
        beamline.amps_changed.connect(seen.append)
        for k in range(40):
            beamline.ingest_labjack_window(_payload(k, 137.0))
        firsts = [s.channels["X+"].wave_kv[0] for s in seen[-8:]]
        assert max(firsts) - min(firsts) < 0.2   # kV, on a 2 kV drive

    def test_axes_are_windowed_independently(self, beamline):
        """A raster runs its two axes decades apart; one shared window would
        alias the fast axis or truncate the slow one."""
        seen = []
        beamline.amps_changed.connect(seen.append)
        for k in range(40):
            payload = _payload(k, 500.0)
            slow = _payload(k, 25.0)
            for amp in ("Y+", "Y-"):
                ain = SC.AMP_CHANNEL_MAP[amp]["voltage"]
                payload["channels"][ain] = slow["channels"][ain]
            beamline.ingest_labjack_window(payload)
        state = seen[-1]
        assert state.channels["X+"].wave_freq_hz == pytest.approx(500.0, rel=0.05)
        assert state.channels["Y+"].wave_freq_hz == pytest.approx(25.0, rel=0.05)
        assert state.channels["Y+"].wave_span_s > state.channels["X+"].wave_span_s

    def test_an_undriven_pair_falls_back_to_the_raw_window(self, beamline):
        """Nothing repeating: show the stream window and report no frequency,
        rather than locking onto noise."""
        ch = _run(beamline, 137.0, peak_v=0.0).channels["X+"]
        assert math.isnan(ch.wave_freq_hz)
        assert ch.wave_span_s == pytest.approx(WINDOW * DT)

    def test_a_channel_with_no_waveform_gets_no_window(self, beamline):
        """Scalar-only payloads (the log-amp-heavy profiles, and every test
        that predates waveforms) must still ingest."""
        seen = []
        beamline.amps_changed.connect(seen.append)
        beamline.ingest_labjack_window({"channels": {}, "t": 1.0})
        ch = seen[-1].channels["X+"]
        assert ch.wave_kv == ()
        assert math.isnan(ch.wave_span_s)

    def test_a_lone_streamed_plate_still_gets_a_window(self, beamline):
        """Single-channel profiles sample one plate of the pair. There is no
        pair relationship left to show, but the survivor's own trace is what
        says the profile is running at all."""
        seen = []
        beamline.amps_changed.connect(seen.append)
        kept = SC.AMP_CHANNEL_MAP["X+"]["voltage"]
        for k in range(40):
            payload = _payload(k, 137.0)
            for ain in list(payload["channels"]):
                if ain != kept:
                    payload["channels"][ain] = None
            beamline.ingest_labjack_window(payload)
        state = seen[-1]
        assert state.channels["X+"].wave_freq_hz == pytest.approx(137.0, rel=0.05)
        assert len(state.channels["X+"].wave_kv) > 2
        assert state.channels["X-"].wave_kv == ()

    def test_history_is_dropped_when_the_stream_restarts(self, beamline):
        """Windows either side of a profile switch were sampled at different
        rates; a period measured across that seam never existed."""
        for k in range(20):
            beamline.ingest_labjack_window(_payload(k, 3.0))
        ains = [SC.AMP_CHANNEL_MAP[a]["voltage"] for a in ("X+", "X-")]
        assert beamline.amp_traces.history.aligned_length(ains) > WINDOW
        beamline._mark_labjack_disconnected()
        assert beamline.amp_traces.history.aligned_length(ains) == 0
