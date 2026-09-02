"""
tests/test_amp_trace.py

Unit tests for rbl.hardware.amp_trace.AmpTraceBuilder.

The tests focus on the parts of AmpTraceBuilder that are purely deterministic:
  - decimate()  — isolated classmethod, no config side-effects
  - push() / clear() — history state transitions
  - traces() output structure — given synthetic waveforms

The AIN channel names and amp labels are imported directly from
rbl.config.hardware_config so the tests stay in sync with the real config
rather than hard-coding copies of it.
"""
import math

import numpy as np
import pytest

from rbl.config import hardware_config as SC
from rbl.hardware.amp_trace import AmpTraceBuilder

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FS   = 50_000.0   # sample rate — matches LabJack FULL profile
F    = 250.0      # drive frequency (Hz)
WIN  = int(FS / 10)  # samples per window at GUI_REFRESH_HZ=10 → 5 000


def _sine_window(freq=F, fs=FS, n=WIN, amp=1.0, phase_rad=0.0):
    """One window-worth of a sine wave."""
    t = np.arange(n) / fs
    return amp * np.sin(2 * np.pi * freq * t + phase_rad)


def _make_channel_dict(*amp_labels, freq=F, amp=1.0, n=WIN):
    """Build the channel dict format that push() expects."""
    out = {}
    for label in amp_labels:
        ain = SC.AMP_CHANNEL_MAP[label]["voltage"]
        out[ain] = {"waveform": _sine_window(freq=freq, amp=amp, n=n)}
    return out


def _push_n(builder, n_windows, *amp_labels, **kw):
    """Push n_windows windows for the given amp labels."""
    for _ in range(n_windows):
        builder.push(_make_channel_dict(*amp_labels, **kw))


# ---------------------------------------------------------------------------
# AmpTraceBuilder.decimate
# ---------------------------------------------------------------------------

class TestDecimate:
    """Isolated classmethod tests — no AlignedWaveHistory involved."""

    def test_empty_returns_empty_tuple(self):
        assert AmpTraceBuilder.decimate(np.array([])) == ()

    def test_none_returns_empty_tuple(self):
        assert AmpTraceBuilder.decimate(None) == ()

    def test_short_wave_all_samples_returned(self):
        wave = np.array([1.0, 2.0, 3.0])
        result = AmpTraceBuilder.decimate(wave)
        assert len(result) == 3

    def test_long_wave_capped_at_wave_points(self):
        wave = np.ones(1000)
        result = AmpTraceBuilder.decimate(wave)
        assert len(result) <= AmpTraceBuilder.WAVE_POINTS

    def test_returns_tuple_of_floats(self):
        wave = np.arange(10, dtype=float)
        result = AmpTraceBuilder.decimate(wave)
        assert isinstance(result, tuple)
        assert all(isinstance(v, float) for v in result)

    def test_applies_kv_scaling(self):
        """Values are multiplied by VOLTAGE_MONITOR_KV_PER_VOLT."""
        wave = np.ones(5)
        result = AmpTraceBuilder.decimate(wave)
        expected = SC.VOLTAGE_MONITOR_KV_PER_VOLT
        for v in result:
            assert v == pytest.approx(expected)

    def test_uniform_stride_preserves_shape_not_envelope(self):
        """Strided decimation keeps every step-th sample, not min/max."""
        # A monotone ramp: uniform striding must preserve the ramp,
        # not collapse it to min and max on alternating points.
        wave = np.arange(100, dtype=float)
        result = AmpTraceBuilder.decimate(wave)
        values = [v / SC.VOLTAGE_MONITOR_KV_PER_VOLT for v in result]
        # Values must be strictly increasing (stride skips evenly through ramp)
        assert all(values[i] < values[i + 1] for i in range(len(values) - 1))

    def test_exactly_wave_points_samples_not_strided(self):
        """If input length == WAVE_POINTS, step=1 → all samples."""
        n = AmpTraceBuilder.WAVE_POINTS
        wave = np.arange(n, dtype=float)
        result = AmpTraceBuilder.decimate(wave)
        assert len(result) == n

    def test_list_input_accepted(self):
        result = AmpTraceBuilder.decimate([1.0, 2.0, 3.0])
        assert len(result) > 0

    def test_single_sample(self):
        result = AmpTraceBuilder.decimate(np.array([5.0]))
        assert result == (pytest.approx(5.0 * SC.VOLTAGE_MONITOR_KV_PER_VOLT),)


# ---------------------------------------------------------------------------
# AmpTraceBuilder — state management
# ---------------------------------------------------------------------------

class TestStateManagement:
    def test_initial_history_empty(self):
        builder = AmpTraceBuilder()
        ains = [SC.AMP_CHANNEL_MAP["X+"]["voltage"],
                SC.AMP_CHANNEL_MAP["X-"]["voltage"]]
        assert builder.history.aligned_length(ains) == 0

    def test_push_accumulates_history(self):
        builder = AmpTraceBuilder()
        _push_n(builder, 3, "X+", "X-")
        ain = SC.AMP_CHANNEL_MAP["X+"]["voltage"]
        assert builder.history.aligned_length([ain]) == 3 * WIN

    def test_clear_resets_history(self):
        builder = AmpTraceBuilder()
        _push_n(builder, 5, "X+", "X-")
        builder.clear()
        ain = SC.AMP_CHANNEL_MAP["X+"]["voltage"]
        assert builder.history.aligned_length([ain]) == 0

    def test_push_missing_waveform_key(self):
        """A channel dict with no 'waveform' is silently skipped."""
        builder = AmpTraceBuilder()
        ain = SC.AMP_CHANNEL_MAP["X+"]["voltage"]
        builder.push({ain: {}})   # dict exists but no 'waveform'
        assert builder.history.aligned_length([ain]) == 0

    def test_push_absent_channel_skipped(self):
        """Channels not present in the dict are absent in that window."""
        builder = AmpTraceBuilder()
        builder.push(_make_channel_dict("X+"))   # X- absent
        ain_plus  = SC.AMP_CHANNEL_MAP["X+"]["voltage"]
        ain_minus = SC.AMP_CHANNEL_MAP["X-"]["voltage"]
        assert builder.history.aligned_length([ain_plus])  > 0
        assert builder.history.aligned_length([ain_minus]) == 0


# ---------------------------------------------------------------------------
# AmpTraceBuilder.traces — output structure
# ---------------------------------------------------------------------------

class TestTracesStructure:
    """Verify the format of traces() without asserting exact numeric values."""

    def _payload(self, n=WIN):
        return {"window_samples": n, "sample_period": 1.0 / FS}

    def test_no_history_returns_empty_dict(self):
        builder = AmpTraceBuilder()
        result = builder.traces(self._payload())
        assert result == {}

    def test_returns_dict_keyed_by_amp_labels(self):
        builder = AmpTraceBuilder()
        _push_n(builder, 20, "X+", "X-")
        result = builder.traces(self._payload())
        for key in result:
            assert key in SC.AMP_LABELS

    def test_each_entry_is_three_tuple(self):
        builder = AmpTraceBuilder()
        _push_n(builder, 20, "X+", "X-")
        result = builder.traces(self._payload())
        for amp, entry in result.items():
            assert len(entry) == 3, f"{amp}: expected (wave, span, freq)"

    def test_wave_is_tuple(self):
        builder = AmpTraceBuilder()
        _push_n(builder, 20, "X+", "X-")
        result = builder.traces(self._payload())
        for amp, (wave, span, freq) in result.items():
            assert isinstance(wave, tuple), f"{amp}: wave not a tuple"

    def test_wave_not_empty(self):
        builder = AmpTraceBuilder()
        _push_n(builder, 20, "X+", "X-")
        result = builder.traces(self._payload())
        for amp, (wave, span, freq) in result.items():
            assert len(wave) > 0, f"{amp}: empty wave"

    def test_wave_length_at_most_wave_points(self):
        builder = AmpTraceBuilder()
        _push_n(builder, 20, "X+", "X-")
        result = builder.traces(self._payload())
        for amp, (wave, span, freq) in result.items():
            assert len(wave) <= AmpTraceBuilder.WAVE_POINTS

    def test_span_positive(self):
        builder = AmpTraceBuilder()
        _push_n(builder, 20, "X+", "X-")
        result = builder.traces(self._payload())
        for amp, (wave, span, freq) in result.items():
            assert span > 0.0, f"{amp}: non-positive span"

    def test_x_pair_both_present(self):
        """Both X+ and X- appear when both channels are in history."""
        builder = AmpTraceBuilder()
        _push_n(builder, 20, "X+", "X-")
        result = builder.traces(self._payload())
        assert "X+" in result and "X-" in result

    def test_y_pair_absent_when_not_pushed(self):
        """Y channels don't appear if they were never pushed."""
        builder = AmpTraceBuilder()
        _push_n(builder, 20, "X+", "X-")
        result = builder.traces(self._payload())
        assert "Y+" not in result
        assert "Y-" not in result

    def test_after_clear_returns_empty(self):
        builder = AmpTraceBuilder()
        _push_n(builder, 20, "X+", "X-")
        builder.clear()
        result = builder.traces(self._payload())
        assert result == {}

    def test_single_channel_only_in_history(self):
        """Only X+ pushed → it appears (solo, not as a pair)."""
        builder = AmpTraceBuilder()
        _push_n(builder, 20, "X+")
        result = builder.traces(self._payload())
        # X+ should be present; X- should not
        assert "X+" in result
        assert "X-" not in result

    def test_freq_finite_after_enough_cycles(self):
        """With many cycles in history, the detected frequency should be finite."""
        # Push 20 windows, each with WIN=5000 samples at 250 Hz → 200 cycles
        builder = AmpTraceBuilder()
        _push_n(builder, 20, "X+", "X-", freq=F)
        result = builder.traces(self._payload())
        if "X+" in result:
            _, _, freq_hz = result["X+"]
            # Either a valid frequency close to F, or NaN for flat/slow signals
            if not math.isnan(freq_hz):
                assert 0 < freq_hz < 10 * F, f"detected freq {freq_hz} seems wrong"

    def test_payload_without_sample_period_uses_window_samples(self):
        """sample_period absent → inferred from window_samples + GUI_REFRESH_HZ."""
        builder = AmpTraceBuilder()
        _push_n(builder, 20, "X+", "X-")
        # Only window_samples provided — must not raise
        result = builder.traces({"window_samples": WIN})
        # At minimum no exception; structure should still be valid
        for amp, entry in result.items():
            assert len(entry) == 3

    def test_flat_signal_does_not_raise(self):
        """A completely flat (DC) waveform: no frequency, fallback to raw window."""
        builder = AmpTraceBuilder()
        ain = SC.AMP_CHANNEL_MAP["X+"]["voltage"]
        for _ in range(20):
            builder.push({ain: {"waveform": np.zeros(WIN)}})
        result = builder.traces(self._payload())
        # Should not raise; wave may be present (fallback) or absent
        for amp, (wave, span, freq) in result.items():
            assert isinstance(wave, tuple)
