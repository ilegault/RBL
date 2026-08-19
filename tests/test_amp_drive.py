"""
tests/test_amp_drive.py
Unit tests for rbl.services.amp_drive.AmpDrive.

Uses a FakeGen that records every call and returns "" from set_waveform
(meaning no warning / no clamp was applied by the driver).
"""
import math
import pytest

from rbl.services.amp_drive import AmpDrive
from rbl.hardware.funcgen_driver import MAX_AMP_VPP
from rbl.hardware.funcgen_safety import _AMP_GAIN
from rbl.config.hardware_config import AMP_MAX_KV


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeGen:
    """Records all calls. set_waveform returns "" (no driver-level warning)."""

    def __init__(self):
        self.calls: list[tuple] = []

    def set_waveform(self, ch, shape, freq, amp, offset, phase):
        self.calls.append(("set_waveform", ch, shape, freq, amp, offset, phase))
        return ""

    def output_on(self, ch):
        self.calls.append(("output_on", ch))

    def output_off(self, ch):
        self.calls.append(("output_off", ch))

    def get_state(self, ch):
        return {
            "shape": "DC", "freq": 0.0, "amp": 0.0,
            "offset": 0.0, "phase": 0.0, "output": "OFF", "load": "INFinity",
        }

    def set_output_load(self, ch, load):
        self.calls.append(("set_output_load", ch, load))

    def set_waveforms(self):
        return [c for c in self.calls if c[0] == "set_waveform"]

    def output_offs(self):
        return [c for c in self.calls if c[0] == "output_off"]

    def output_ons(self):
        return [c for c in self.calls if c[0] == "output_on"]


def _drive_with_fakes(max_kv=AMP_MAX_KV):
    """Return (AmpDrive, fake_a, fake_b) with a standard 4-amp funcgen_map."""
    fa, fb = FakeGen(), FakeGen()
    fmap = {
        "X+": (fa, 1), "X-": (fa, 2),
        "Y+": (fb, 1), "Y-": (fb, 2),
    }
    return AmpDrive(fmap, max_kv=max_kv, log_prefix="[TEST]"), fa, fb


# ---------------------------------------------------------------------------
# command_dc_ramped / command_ac_amplitude_ramped
# ---------------------------------------------------------------------------

class FakeRampEngine:
    """Records retarget() calls; no Qt, no timers."""

    def __init__(self):
        self.calls: list[tuple] = []

    def retarget(self, label, target_v, mode="offset"):
        self.calls.append((label, target_v, mode))


class TestRampedCommands:
    def test_command_dc_ramped_requires_attached_engine(self):
        drive, _, _ = _drive_with_fakes()
        with pytest.raises(RuntimeError):
            drive.command_dc_ramped("X+", 2.0)

    def test_command_dc_ramped_converts_kv_to_generator_volts(self):
        drive, _, _ = _drive_with_fakes()
        ramp = FakeRampEngine()
        drive.attach_ramp_engine(ramp)
        drive.command_dc_ramped("X+", 2.5)
        label, target_v, mode = ramp.calls[0]
        assert label == "X+"
        assert mode == "offset"
        assert target_v == pytest.approx(2.5 * 1000.0 / _AMP_GAIN)

    def test_command_dc_ramped_clamps_to_max_kv(self):
        drive, _, _ = _drive_with_fakes(max_kv=5.0)
        ramp = FakeRampEngine()
        drive.attach_ramp_engine(ramp)
        drive.command_dc_ramped("X+", 9.0)
        _, target_v, _ = ramp.calls[0]
        assert target_v == pytest.approx(5.0 * 1000.0 / _AMP_GAIN)

    def test_command_ac_amplitude_ramped_requires_attached_engine(self):
        drive, _, _ = _drive_with_fakes()
        with pytest.raises(RuntimeError):
            drive.command_ac_amplitude_ramped("X+", 2.0)

    def test_command_ac_amplitude_ramped_converts_peak_kv_to_vpp(self):
        drive, _, _ = _drive_with_fakes()
        ramp = FakeRampEngine()
        drive.attach_ramp_engine(ramp)
        drive.command_ac_amplitude_ramped("X+", 1.5)
        label, target_v, mode = ramp.calls[0]
        assert label == "X+"
        assert mode == "amplitude"
        assert target_v == pytest.approx(1.5 * 2.0 * 1000.0 / _AMP_GAIN)

    def test_command_ac_amplitude_ramped_clamps_negative_to_zero(self):
        drive, _, _ = _drive_with_fakes()
        ramp = FakeRampEngine()
        drive.attach_ramp_engine(ramp)
        drive.command_ac_amplitude_ramped("X+", -1.0)
        _, target_v, _ = ramp.calls[0]
        assert target_v == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# command_dc
# ---------------------------------------------------------------------------

class TestCommandDc:
    def test_normal_value_passes_through(self):
        drive, fa, _ = _drive_with_fakes()
        drive.command_dc("X+", 2.5)
        sw = fa.set_waveforms()[0]
        expected_gen_v = 2.5 * 1000.0 / _AMP_GAIN
        assert abs(sw[5] - expected_gen_v) < 1e-9   # offset arg

    def test_clamps_above_max_kv(self):
        drive, fa, _ = _drive_with_fakes(max_kv=5.0)
        drive.command_dc("X+", 9.0)
        sw = fa.set_waveforms()[0]
        expected_gen_v = 5.0 * 1000.0 / _AMP_GAIN
        assert abs(sw[5] - expected_gen_v) < 1e-9

    def test_clamps_below_negative_max_kv(self):
        drive, fa, _ = _drive_with_fakes(max_kv=5.0)
        drive.command_dc("X+", -9.0)
        sw = fa.set_waveforms()[0]
        expected_gen_v = -5.0 * 1000.0 / _AMP_GAIN
        assert abs(sw[5] - expected_gen_v) < 1e-9

    def test_shape_is_dc(self):
        drive, fa, _ = _drive_with_fakes()
        drive.command_dc("X+", 1.0)
        assert fa.set_waveforms()[0][2] == "DC"

    def test_output_on_called(self):
        drive, fa, _ = _drive_with_fakes()
        drive.command_dc("X+", 1.0)
        assert any(c[0] == "output_on" for c in fa.calls)

    def test_re_raises_on_gen_exception(self):
        class _BrokenGen(FakeGen):
            def set_waveform(self, *a):
                raise RuntimeError("hw fault")
        fa = _BrokenGen()
        fmap = {"X+": (fa, 1), "X-": (fa, 2), "Y+": (FakeGen(), 1), "Y-": (FakeGen(), 2)}
        drive = AmpDrive(fmap, max_kv=5.0)
        with pytest.raises(RuntimeError, match="hw fault"):
            drive.command_dc("X+", 1.0)


# ---------------------------------------------------------------------------
# command_sine
# ---------------------------------------------------------------------------

class TestCommandSine:
    def test_exact_vpp_at_max_kv(self):
        drive, fa, _ = _drive_with_fakes(max_kv=5.0)
        drive.command_sine("X+", 5.0, 1000.0)
        sw = fa.set_waveforms()[0]
        expected_vpp = 5.0 * 2.0 * 1000.0 / _AMP_GAIN
        assert abs(sw[4] - expected_vpp) < 1e-9
        assert abs(expected_vpp - MAX_AMP_VPP) < 1e-9   # sanity: that IS MAX_AMP_VPP

    def test_clamps_peak_above_max_kv(self):
        drive, fa, _ = _drive_with_fakes(max_kv=5.0)
        drive.command_sine("X+", 6.0, 1000.0)
        sw = fa.set_waveforms()[0]
        assert sw[4] <= MAX_AMP_VPP + 1e-9

    def test_shape_is_Sine(self):
        drive, fa, _ = _drive_with_fakes()
        drive.command_sine("X+", 1.0, 500.0)
        assert fa.set_waveforms()[0][2] == "Sine"

    def test_freq_is_passed_through(self):
        drive, fa, _ = _drive_with_fakes()
        drive.command_sine("X+", 1.0, 1234.5)
        assert abs(fa.set_waveforms()[0][3] - 1234.5) < 1e-9

    def test_offset_is_zero(self):
        drive, fa, _ = _drive_with_fakes()
        drive.command_sine("X+", 2.0, 1000.0)
        assert fa.set_waveforms()[0][5] == 0.0

    def test_output_on_called(self):
        drive, fa, _ = _drive_with_fakes()
        drive.command_sine("X+", 1.0, 1000.0)
        assert any(c[0] == "output_on" for c in fa.calls)

    def test_negative_peak_clamped_to_zero(self):
        drive, fa, _ = _drive_with_fakes()
        drive.command_sine("X+", -1.0, 1000.0)
        sw = fa.set_waveforms()[0]
        assert sw[4] == 0.0


# ---------------------------------------------------------------------------
# command_square
# ---------------------------------------------------------------------------

class TestCommandSquare:
    def test_shape_is_Square(self):
        drive, fa, _ = _drive_with_fakes()
        drive.command_square("X+", 2.0, 500.0)
        assert fa.set_waveforms()[0][2] == "Square"

    def test_vpp_correct(self):
        drive, fa, _ = _drive_with_fakes(max_kv=5.0)
        drive.command_square("X+", 2.0, 500.0)
        expected_vpp = 2.0 * 2.0 * 1000.0 / _AMP_GAIN
        assert abs(fa.set_waveforms()[0][4] - expected_vpp) < 1e-9


# ---------------------------------------------------------------------------
# zero_and_off_all
# ---------------------------------------------------------------------------

class TestZeroAndOffAll:
    def test_all_four_channels_zeroed(self):
        drive, fa, fb = _drive_with_fakes()
        drive.zero_and_off_all()
        dc_calls = [c for c in fa.calls + fb.calls if c[0] == "set_waveform"]
        assert len(dc_calls) == 4
        for c in dc_calls:
            assert c[2] == "DC"
            assert c[5] == 0.0   # offset (DC level)

    def test_all_four_outputs_off(self):
        drive, fa, fb = _drive_with_fakes()
        drive.zero_and_off_all()
        off_calls = fa.output_offs() + fb.output_offs()
        assert len(off_calls) == 4

    def test_continues_past_one_set_waveform_fault(self):
        """A set_waveform failure on channel 1 must not prevent the other
        three channels from being zeroed and their outputs turned off."""
        class _FaultOnCh1(FakeGen):
            def set_waveform(self, ch, *a, **kw):
                if ch == 1:
                    raise RuntimeError("simulated fault on ch1")
                return super().set_waveform(ch, *a, **kw)

        fa = _FaultOnCh1()
        fb = FakeGen()
        fmap = {"X+": (fa, 1), "X-": (fa, 2), "Y+": (fb, 1), "Y-": (fb, 2)}
        drive = AmpDrive(fmap, max_kv=5.0)

        drive.zero_and_off_all()   # must not raise

        # fa ch2 and both fb channels must still get output_off
        off_on_fb = fb.output_offs()
        assert len(off_on_fb) == 2, f"expected 2 output_off on fb, got {off_on_fb}"


# ---------------------------------------------------------------------------
# restore_all
# ---------------------------------------------------------------------------

class TestRestoreAll:
    def test_never_calls_output_on(self):
        drive, fa, fb = _drive_with_fakes()
        snap = drive.snapshot_all()
        fa.calls.clear()
        fb.calls.clear()
        drive.restore_all(snap)
        for gen in (fa, fb):
            assert not gen.output_ons(), \
                f"restore_all must not call output_on; got {gen.calls}"

    def test_restores_waveform_parameters(self):
        fa = FakeGen()
        fb = FakeGen()
        snap = {
            "X+": {"shape": "Sine", "freq": 1000.0, "amp": 2.0,
                   "offset": 0.0, "phase": 0.0, "load": "INFinity"},
            "X-": None,
            "Y+": {"shape": "DC", "freq": 0.0, "amp": 0.0,
                   "offset": 0.5, "phase": 0.0, "load": "INFinity"},
            "Y-": None,
        }
        fmap = {"X+": (fa, 1), "X-": (fa, 2), "Y+": (fb, 1), "Y-": (fb, 2)}
        drive = AmpDrive(fmap, max_kv=5.0)
        drive.restore_all(snap)

        # X+ restored
        xp = fa.set_waveforms()[0]
        assert xp[2] == "Sine" and abs(xp[3] - 1000.0) < 1e-9

        # X- skipped (None snapshot)
        assert len(fa.set_waveforms()) == 1

    def test_skips_error_entries(self):
        drive, fa, _ = _drive_with_fakes()
        snap = {"X+": {"error": "timed out"}, "X-": None, "Y+": None, "Y-": None}
        fa.calls.clear()
        drive.restore_all(snap)
        assert not fa.set_waveforms()


# ---------------------------------------------------------------------------
# snapshot_all
# ---------------------------------------------------------------------------

class TestSnapshotAll:
    def test_returns_dict_for_all_labels(self):
        drive, _, _ = _drive_with_fakes()
        snap = drive.snapshot_all()
        assert set(snap.keys()) == {"X+", "X-", "Y+", "Y-"}

    def test_failed_get_state_returns_none(self):
        class _BadGen(FakeGen):
            def get_state(self, ch):
                raise RuntimeError("timeout")
        fa = _BadGen()
        fmap = {"X+": (fa, 1), "X-": (fa, 2), "Y+": (FakeGen(), 1), "Y-": (FakeGen(), 2)}
        drive = AmpDrive(fmap, max_kv=5.0)
        snap = drive.snapshot_all()
        assert snap["X+"] is None
        assert snap["X-"] is None
