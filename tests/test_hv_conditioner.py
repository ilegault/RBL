"""
Tests for rbl.services.hv_conditioner.HvConditioner.

No hardware: a FakeGen stands in for DG1022Z, driven through a real
RampEngine (small ramp_duration_s so tests run fast). QTimer slots are
invoked directly rather than by running the Qt event loop, matching this
repo's convention in tests/test_calibration_runner.py.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from rbl.services.hv_conditioner import HvConditioner
from rbl.services.ramp_engine import RampEngine


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class FakeGen:
    def __init__(self):
        self._offset = {1: 0.0, 2: 0.0}
        self._amp = {1: 0.0, 2: 0.0}

    def get_error(self):
        return '0,"No error"'

    def get_state(self, ch):
        return {"shape": "DC", "freq": 0.0, "amp": self._amp[ch],
                 "offset": self._offset[ch], "phase": 0.0,
                 "output": True, "load": "INFinity"}

    def set_offset(self, ch, v):
        self._offset[ch] = v

    def set_amplitude(self, ch, vpp):
        self._amp[ch] = vpp

    def write_fast(self, cmd):
        value = float(cmd.split()[-1])
        ch = int(cmd.split(":")[1].replace("SOURce", ""))
        if "OFFSet" in cmd:
            self._offset[ch] = value
        else:
            self._amp[ch] = value

    def set_waveform(self, ch, shape, freq, amp, offset, phase):
        self._offset[ch] = offset
        return ""

    def output_on(self, ch):
        pass

    def output_off(self, ch):
        pass

    def set_output_load(self, ch, load):
        pass


def _drive_ramp_to_completion(cond, label="X+", max_ticks=2000):
    for _ in range(max_ticks):
        if not cond._ramp_engine.is_ramping(label):
            return
        cond._ramp_engine._tick()
    raise AssertionError("ramp did not complete")


def _run_until_finished_or_dwell(cond, max_steps=2000):
    """Drive ramp ticks and dwell completions until the conditioner either
    finishes or is waiting on a dwell whose window data the test wants to
    control — returns once a dwell timer is armed and the ramp has settled."""
    for _ in range(max_steps):
        if not cond._running:
            return
        if cond._ramp_engine.is_ramping("X+"):
            cond._ramp_engine._tick()
            continue
        if cond._dwell_timer.isActive():
            return
    raise AssertionError("conditioner never reached a dwell")


@pytest.fixture
def setup(qapp):
    gen = FakeGen()
    fmap = {"X+": (gen, 1)}
    ramp = RampEngine(fmap, ramp_duration_s=0.01)
    cond = HvConditioner("X+", fmap, ramp)
    return cond, gen, ramp


class TestReachesTarget:
    def test_no_excursions_reaches_target_cleanly(self, setup):
        cond, gen, ramp = setup
        finished = []
        cond.finished.connect(finished.append)
        cond.start(target_kv=1.0, increment_kv=0.5, dwell_s=0.01,
                   clean_dwells_to_advance=1)

        for _ in range(4000):
            if not cond._running:
                break
            if ramp.is_ramping("X+"):
                ramp._tick()
                continue
            if cond._dwell_timer.isActive():
                cond._on_dwell_complete()

        assert finished
        record = finished[0]
        assert record["achieved_kv"] == pytest.approx(1.0, abs=1e-6)
        assert record["reason"] == "target reached"
        assert record["events"] == []
        assert record["curve"][0] == (0.0, 0.0)

    def test_multiple_clean_dwells_required_before_advancing(self, setup):
        cond, gen, ramp = setup
        # Two levels (0.5, then 1.0 kV) so the first level's clean-dwell
        # count actually gates advancement rather than immediately hitting
        # the target on the very first dwell.
        cond.start(target_kv=1.0, increment_kv=0.5, dwell_s=0.01,
                   clean_dwells_to_advance=3)
        _run_until_finished_or_dwell(cond)
        assert cond._level_kv == pytest.approx(0.5)
        cond._on_dwell_complete()
        assert cond._clean_dwells_at_level == 1
        assert cond._level_kv == pytest.approx(0.5)   # still at the first level
        cond._on_dwell_complete()
        assert cond._clean_dwells_at_level == 2
        assert cond._level_kv == pytest.approx(0.5)
        cond._on_dwell_complete()
        # 3rd clean dwell advances to the next (and final) level.
        _run_until_finished_or_dwell(cond)
        assert cond._level_kv == pytest.approx(1.0)


class TestExcursionBackoff:
    def test_excursion_logs_event_and_backs_off(self, setup):
        cond, gen, ramp = setup
        events = []
        cond.discharge_event.connect(events.append)
        cond.start(target_kv=1.0, increment_kv=0.5, dwell_s=0.01,
                   clean_dwells_to_advance=1, excursion_ma=20.0)
        _run_until_finished_or_dwell(cond)
        assert cond._level_kv == pytest.approx(0.5)

        # Simulate a large current excursion during this dwell window: raw
        # monitor volts of 5.0 -> 50 mA (1 V = 10 mA), well above 20 mA.
        cond.on_window({"channels": {"AIN12": {"waveform": np.full(100, 5.0)}}})
        cond._on_dwell_complete()

        assert events and events[0]["level_kv"] == pytest.approx(0.5)
        assert events[0]["peak_current_ma"] == pytest.approx(50.0)
        # Backed off by back_off_increments(2) * increment(0.5) = 1.0, floored at 0.
        assert cond._level_kv == pytest.approx(0.0)

    def test_repeated_excursions_at_same_level_yield_conditioned_ceiling(self, setup):
        cond, gen, ramp = setup
        finished = []
        cond.finished.connect(finished.append)
        cond.start(target_kv=1.0, increment_kv=0.5, dwell_s=0.01,
                   clean_dwells_to_advance=1, max_retries_per_level=2,
                   excursion_ma=20.0)

        for _ in range(6):
            _run_until_finished_or_dwell(cond)
            if not cond._running:
                break
            cond.on_window({"channels": {"AIN12": {"waveform": np.full(100, 5.0)}}})
            cond._on_dwell_complete()

        assert finished
        assert "conditioned ceiling" in finished[0]["reason"]

    def test_regulation_state_alone_can_trigger_an_excursion(self, setup):
        cond, gen, ramp = setup
        cond._regulation_state_provider = lambda: "current_limited"
        events = []
        cond.discharge_event.connect(events.append)
        cond.start(target_kv=1.0, increment_kv=0.5, dwell_s=0.01,
                   clean_dwells_to_advance=1)
        _run_until_finished_or_dwell(cond)
        cond._on_dwell_complete()   # no current data at all, but regulation says bad
        assert events
        assert events[0]["regulation_state"] == "current_limited"


class TestInterlock:
    def test_interlock_block_aborts_before_ramping(self, setup):
        cond, gen, ramp = setup
        cond._hv_interlock_status_provider = lambda kv: ("block", "vacuum too high")
        finished = []
        cond.finished.connect(finished.append)
        cond.start(target_kv=1.0, increment_kv=0.5, dwell_s=0.01)
        assert finished
        assert "interlock blocked" in finished[0]["reason"]
        assert not ramp.is_ramping("X+")

    def test_pressure_is_recorded_at_every_dwell(self, setup):
        cond, gen, ramp = setup
        cond._pressure_provider = lambda: 2.5e-6
        events = []
        cond.discharge_event.connect(events.append)
        cond.start(target_kv=1.0, increment_kv=0.5, dwell_s=0.01,
                   clean_dwells_to_advance=1)
        _run_until_finished_or_dwell(cond)
        cond.on_window({"channels": {"AIN12": {"waveform": np.full(100, 5.0)}}})
        cond._on_dwell_complete()
        assert events[0]["pressure_torr"] == 2.5e-6


class TestAbort:
    def test_abort_mid_dwell_finishes_and_zeros(self, setup):
        cond, gen, ramp = setup
        finished = []
        cond.finished.connect(finished.append)
        cond.start(target_kv=1.0, increment_kv=0.5, dwell_s=0.01)
        _run_until_finished_or_dwell(cond)
        cond.abort()
        assert finished
        assert finished[0]["reason"] == "aborted"
        assert not cond._dwell_timer.isActive()

    def test_abort_when_not_running_is_a_noop(self, setup):
        cond, gen, ramp = setup
        cond.abort()   # must not raise
