"""
Tests for rbl.services.dynamic_adjustment.DynamicAdjustmentTrial.

No hardware: a FakeGen stands in for DG1022Z, and synthetic single-channel
window payloads are injected directly via on_window().
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest

from PySide6.QtWidgets import QApplication

from rbl.config.hardware_config import AMP_CHANNEL_MAP
from rbl.services.dynamic_adjustment import (
    TRIAL_FREQ_HZ,
    TRIAL_PEAK_KV,
    TRIAL_STREAM_S,
    DynamicAdjustmentTrial,
    _average_edge_window,
    _find_edges,
)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class FakeGen:
    def __init__(self):
        self.calls = []

    def set_waveform(self, ch, shape, freq, amp, offset, phase):
        self.calls.append(("set_waveform", ch, shape, freq, amp, offset, phase))
        return ""

    def output_on(self, ch):
        self.calls.append(("output_on", ch))

    def output_off(self, ch):
        self.calls.append(("output_off", ch))

    def get_state(self, ch):
        return {"shape": "DC", "freq": 0.0, "amp": 0.0, "offset": 0.0,
                 "phase": 0.0, "output": False, "load": "INFinity"}

    def set_output_load(self, ch, load):
        pass


@pytest.fixture
def funcgen_map():
    return {"X+": (FakeGen(), 1)}


def _feed_square_wave(trial, v_or_i_trace, fs, stop_stage, chunk=2000):
    """Feed windows until trial._stage advances past the stage this data is
    for. `stop_stage` is the stage we're feeding data FOR — the loop stops
    once trial._stage no longer equals it (it moved on)."""
    n = v_or_i_trace.size
    ain = trial.target_ain()
    for w in range(0, n, chunk):
        payload = {"sample_period": 1.0 / fs,
                   "channels": {ain: {"waveform": v_or_i_trace[w:w+chunk]}}}
        trial.on_window(payload)
        if trial._stage != stop_stage:
            break


class TestConstruction:
    def test_blank_pot_position_raises(self, qapp, funcgen_map):
        with pytest.raises(ValueError):
            DynamicAdjustmentTrial("X+", funcgen_map, pot_position="")

    def test_whitespace_only_pot_position_raises(self, qapp, funcgen_map):
        with pytest.raises(ValueError):
            DynamicAdjustmentTrial("X+", funcgen_map, pot_position="   ")

    def test_valid_pot_position_is_stored_and_stripped(self, qapp, funcgen_map):
        trial = DynamicAdjustmentTrial("X+", funcgen_map, pot_position="  2 o'clock  ")
        assert trial.pot_position == "2 o'clock"


class TestStageSequencing:
    def test_starts_on_voltage_stage_targeting_voltage_ain(self, qapp, funcgen_map):
        trial = DynamicAdjustmentTrial("X+", funcgen_map, pot_position="A")
        stages = []
        trial.stage_changed.connect(stages.append)
        trial.start()
        assert stages == ["voltage"]
        assert trial.target_ain() == AMP_CHANNEL_MAP["X+"]["voltage"]

    def test_commands_a_low_amplitude_square_wave(self, qapp, funcgen_map):
        gen, _ = funcgen_map["X+"]
        trial = DynamicAdjustmentTrial("X+", funcgen_map, pot_position="A")
        trial.start()
        sw = next(c for c in gen.calls if c[0] == "set_waveform")
        assert sw[2] == "Square"
        # peak_kv=0.5 -> gen_vpp = 0.5*2*1000/1000 = 1.0 Vpp
        assert sw[4] == pytest.approx(1.0)


class TestFullTrialPipeline:
    def _run_full_trial(self, funcgen_map, pot_position="3 o'clock", creep_frac=0.03):
        trial = DynamicAdjustmentTrial("X+", funcgen_map, pot_position=pot_position)
        completed = []
        trial.trial_completed.connect(completed.append)
        trial.start()

        fs = 100_000.0
        n = int(fs * TRIAL_STREAM_S)
        t = np.arange(n) / fs
        half_period = 0.5 / TRIAL_FREQ_HZ
        t_since_edge = t % half_period
        base = TRIAL_PEAK_KV * np.sign(np.sin(2 * np.pi * TRIAL_FREQ_HZ * t))
        # Creep modeled per-edge (relative to the most recent transition),
        # not against absolute stream time — an exponential rise within each
        # half-period that reinforces (never cancels) across edges once the
        # averager flips alternating (falling) edges back to "rising".
        creep = creep_frac * TRIAL_PEAK_KV * (1 - np.exp(-t_since_edge / 0.05)) * np.sign(base)
        v_kv = base + creep + np.random.default_rng(0).normal(0, 0.0005, n)
        _feed_square_wave(trial, v_kv, fs, stop_stage="voltage")

        # A clean periodic decaying pulse at every half-period boundary —
        # always the same shape/magnitude, so the percentile-threshold edge
        # detector (designed for a real amplifier's charge/discharge pulse
        # train) has an unambiguous signal to lock onto.
        i_ma = 10.0 * np.exp(-t_since_edge / 2e-4) + 0.1
        _feed_square_wave(trial, i_ma / 10.0, fs, stop_stage="current")

        assert completed, "trial did not complete"
        return completed[0]

    def test_undercompensated_creep_is_positive(self, qapp, funcgen_map):
        record = self._run_full_trial(funcgen_map, creep_frac=0.05)
        assert record["voltage"]["flat_top_creep_pct"] > 0.5

    def test_overcompensated_creep_is_negative(self, qapp, funcgen_map):
        record = self._run_full_trial(funcgen_map, creep_frac=-0.05)
        assert record["voltage"]["flat_top_creep_pct"] < -0.5

    def test_record_carries_pot_position_and_both_stages(self, qapp, funcgen_map):
        record = self._run_full_trial(funcgen_map, pot_position="5 o'clock")
        assert record["pot_position"] == "5 o'clock"
        assert "overshoot_pct" in record["voltage"]
        assert "peak_current_ma" in record["current"]
        assert "figure_of_merit" in record

    def test_decimated_traces_are_stored(self, qapp, funcgen_map):
        record = self._run_full_trial(funcgen_map)
        assert len(record["voltage"]["trace_v_kv"]) > 0
        assert len(record["current"]["trace_i_ma"]) > 0
        from rbl.services.dynamic_adjustment import DECIMATED_TRACE_POINTS
        assert len(record["voltage"]["trace_v_kv"]) <= DECIMATED_TRACE_POINTS + 10

    def test_amplifier_output_is_zeroed_after_trial(self, qapp, funcgen_map):
        gen, ch = funcgen_map["X+"]
        self._run_full_trial(funcgen_map)
        assert any(c[0] == "output_off" for c in gen.calls)


class TestEdgeDetectionHelpers:
    def test_find_edges_on_a_clean_square_wave(self):
        fs = 100_000.0
        t = np.arange(0, 3.0, 1.0 / fs)
        v = np.sign(np.sin(2 * np.pi * 1.0 * t))
        edges = _find_edges(v, min_spacing_samples=int(0.1 * fs))
        assert len(edges) >= 4

    def test_find_edges_on_flat_signal_is_empty(self):
        v = np.full(1000, 1.0)
        assert _find_edges(v, min_spacing_samples=10).size == 0

    def test_average_edge_window_flips_falling_edges(self):
        fs = 10_000.0
        t = np.arange(0, 2.0, 1.0 / fs)
        v = np.sign(np.sin(2 * np.pi * 1.0 * t))
        edges = _find_edges(v, min_spacing_samples=int(0.1 * fs))
        result = _average_edge_window(v, edges, 1.0 / fs, n_pre=10, n_post=100)
        assert result is not None
        t_win, avg, n = result
        assert avg[-1] > 0   # every edge reads as "rising" after flipping

    def test_average_edge_window_none_when_no_edges(self):
        assert _average_edge_window(np.zeros(100), np.array([]), 1e-4, 5, 5) is None
