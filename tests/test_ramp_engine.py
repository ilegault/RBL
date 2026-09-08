"""
Tests for rbl.services.ramp_engine.RampEngine. Qt timer slots are invoked
directly (``engine._tick()``) rather than by running the Qt event loop,
matching this repo's convention in tests/test_calibration_runner.py — nothing
here blocks, ever.

RampEngine works in raw generator volts/Vpp (the same units
`set_offset`/`set_amplitude` take), not plate kV — see its module docstring.
"""
import pytest

from PySide6.QtWidgets import QApplication

from rbl.hardware.funcgen_driver import MAX_AMP_VPP, MAX_GEN_VOLTS
from rbl.services.ramp_engine import RampEngine


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class FakeGen:
    """Mirrors DG1022Z's amplitude/offset/write_fast contract without a real
    instrument, the same technique test_calibration_runner.py uses for the
    full driver."""

    def __init__(self):
        self.checked_calls = []
        self.fast_calls = []
        self._offset = {1: 0.0, 2: 0.0}
        self._amp = {1: 0.0, 2: 0.0}
        self.fail_offset_for_channel = None

    def get_error(self):
        return '0,"No error"'

    def get_state(self, ch):
        return {"shape": "DC", "freq": 0.0, "amp": self._amp[ch],
                 "offset": self._offset[ch], "phase": 0.0,
                 "output": True, "load": "INFinity"}

    def set_offset(self, ch, v):
        if ch == self.fail_offset_for_channel:
            raise RuntimeError("simulated SCPI failure")
        self.checked_calls.append(("set_offset", ch, v))
        self._offset[ch] = v

    def set_amplitude(self, ch, vpp):
        self.checked_calls.append(("set_amplitude", ch, vpp))
        self._amp[ch] = vpp

    def write_fast(self, cmd):
        self.fast_calls.append(cmd)
        value = float(cmd.split()[-1])
        ch = int(cmd.split(":")[1].replace("SOURce", ""))
        if "OFFSet" in cmd:
            self._offset[ch] = value
        else:
            self._amp[ch] = value


def run_to_completion(engine, label, max_ticks=500):
    for _ in range(max_ticks):
        if not engine.is_ramping(label):
            return
        engine._tick()
    raise AssertionError(f"{label} did not finish ramping within {max_ticks} ticks")


class TestOffsetRamp:
    def test_reaches_target_voltage(self, qapp):
        gen = FakeGen()
        engine = RampEngine({"X+": (gen, 1)}, ramp_duration_s=0.05)
        engine.retarget("X+", 3.0, mode="offset")
        run_to_completion(engine, "X+")
        assert gen._offset[1] == pytest.approx(3.0, abs=1e-6)

    def test_final_step_uses_checked_write(self, qapp):
        gen = FakeGen()
        engine = RampEngine({"X+": (gen, 1)}, ramp_duration_s=0.05)
        engine.retarget("X+", 3.0, mode="offset")
        run_to_completion(engine, "X+")
        assert any(c[0] == "set_offset" for c in gen.checked_calls)

    def test_intermediate_steps_use_write_fast_not_apply(self, qapp):
        gen = FakeGen()
        engine = RampEngine({"X+": (gen, 1)}, ramp_duration_s=0.5)
        engine.retarget("X+", 3.0, mode="offset")
        engine._tick()
        assert gen.fast_calls, "expected at least one write_fast step before completion"
        assert not any("APPLy" in c for c in gen.fast_calls)

    def test_emits_started_progress_finished(self, qapp):
        gen = FakeGen()
        engine = RampEngine({"X+": (gen, 1)}, ramp_duration_s=0.05)
        started, finished, progress = [], [], []
        engine.ramp_started.connect(started.append)
        engine.ramp_finished.connect(finished.append)
        engine.ramp_progress.connect(lambda *a: progress.append(a))
        engine.retarget("X+", 2.0, mode="offset")
        run_to_completion(engine, "X+")
        assert started == ["X+"]
        assert finished == ["X+"]
        assert progress

    def test_target_is_clamped_to_max_gen_volts(self, qapp):
        gen = FakeGen()
        engine = RampEngine({"X+": (gen, 1)}, ramp_duration_s=0.05)
        engine.retarget("X+", MAX_GEN_VOLTS + 10.0, mode="offset")
        run_to_completion(engine, "X+")
        assert gen._offset[1] == pytest.approx(MAX_GEN_VOLTS, abs=1e-6)


class TestAmplitudeRamp:
    def test_reaches_target_vpp(self, qapp):
        gen = FakeGen()
        engine = RampEngine({"X+": (gen, 1)}, ramp_duration_s=0.05)
        engine.retarget("X+", 2.0, mode="amplitude")
        run_to_completion(engine, "X+")
        assert gen._amp[1] == pytest.approx(2.0, abs=1e-6)

    def test_target_is_clamped_to_max_amp_vpp(self, qapp):
        gen = FakeGen()
        engine = RampEngine({"X+": (gen, 1)}, ramp_duration_s=0.05)
        engine.retarget("X+", MAX_AMP_VPP + 10.0, mode="amplitude")
        run_to_completion(engine, "X+")
        assert gen._amp[1] == pytest.approx(MAX_AMP_VPP, abs=1e-6)


class TestRetargetMidRamp:
    def test_updates_in_place_rather_than_queuing(self, qapp):
        gen = FakeGen()
        engine = RampEngine({"X+": (gen, 1)}, ramp_duration_s=0.05)
        engine.retarget("X+", 1.0, mode="offset")
        engine._tick()
        engine.retarget("X+", 4.0, mode="offset")
        assert engine._ramps["X+"]["target_v"] == 4.0
        run_to_completion(engine, "X+")
        assert gen._offset[1] == pytest.approx(4.0, abs=1e-6)


class TestLockstepOrdering:
    def test_pair_members_stepped_adjacent_and_in_canonical_order(self, qapp):
        gen = FakeGen()
        engine = RampEngine({"Y-": (gen, 2), "X+": (gen, 1)}, ramp_duration_s=0.5)
        engine.retarget("Y-", 1.0, mode="offset")
        engine.retarget("X+", 1.0, mode="offset")
        order_seen = []
        engine.ramp_progress.connect(lambda label, *_: order_seen.append(label))
        engine._tick()
        assert order_seen == ["X+", "Y-"]


class TestAbort:
    def test_clears_all_ramps_without_commanding(self, qapp):
        gen = FakeGen()
        engine = RampEngine({"X+": (gen, 1), "X-": (gen, 2)})
        engine.retarget("X+", 3.0, mode="offset")
        engine.retarget("X-", 3.0, mode="offset")
        engine.abort()
        assert not engine.ramping_labels()
        assert not engine._timer.isActive()


class TestFailurePropagation:
    def test_scpi_failure_on_final_step_emits_ramp_failed(self, qapp):
        gen = FakeGen()
        gen.fail_offset_for_channel = 1
        engine = RampEngine({"X+": (gen, 1)}, ramp_duration_s=0.05)
        failures = []
        engine.ramp_failed.connect(lambda label, msg: failures.append((label, msg)))
        engine.retarget("X+", 3.0, mode="offset")
        for _ in range(500):
            if not engine.is_ramping("X+"):
                break
            engine._tick()
        assert failures and failures[0][0] == "X+"


class TestCurrentStepValue:
    def test_none_when_not_ramping(self, qapp):
        gen = FakeGen()
        engine = RampEngine({"X+": (gen, 1)})
        assert engine.current_step_value("X+") is None

    def test_tracks_interim_value_while_ramping(self, qapp):
        gen = FakeGen()
        engine = RampEngine({"X+": (gen, 1)}, ramp_duration_s=1.0)
        engine.retarget("X+", 5.0, mode="offset")
        engine._tick()
        step_v = engine.current_step_value("X+")
        assert step_v is not None
        assert 0.0 < step_v < 5.0


class TestUnknownChannel:
    def test_emits_ramp_failed_for_unmapped_label(self, qapp):
        engine = RampEngine({})
        failures = []
        engine.ramp_failed.connect(lambda label, msg: failures.append((label, msg)))
        engine.retarget("Z+", 1.0, mode="offset")
        assert failures and failures[0][0] == "Z+"
        assert not engine.is_ramping("Z+")
