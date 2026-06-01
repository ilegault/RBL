"""Tests for rbl.physics — amplifier model and deflection physics."""
import pytest
import numpy as np

from rbl.physics.amplifier import (
    mm_to_kV,
    kV_to_mm,
    apply_lowpass_fft,
    apply_slew_limit,
    apply_amplifier,
    required_slew_rate_V_per_s,
)
from rbl.physics.deflection_physics import calculate_drive_for_deflection


# ── Unit-conversion helpers ───────────────────────────────────────────────────

class TestUnitConversions:
    def test_mm_to_kV_round_trip(self):
        kV_per_mm = 0.368
        mm = 15.0
        assert abs(kV_to_mm(mm_to_kV(mm, kV_per_mm), kV_per_mm) - mm) < 1e-12

    def test_mm_to_kV_zero(self):
        assert mm_to_kV(0.0, 0.368) == 0.0

    def test_kV_to_mm_proportional(self):
        assert abs(kV_to_mm(2.0, 0.5) - 4.0) < 1e-12


# ── apply_lowpass_fft ─────────────────────────────────────────────────────────

class TestLowpassFFT:
    def test_dc_signal_unchanged(self):
        signal = np.ones(1000) * 5.0
        dt = 1e-5
        out = apply_lowpass_fft(signal, dt, f_3dB_hz=1000.0)
        assert np.allclose(out, signal, atol=0.01)

    def test_well_below_cutoff_amplitude_retained(self):
        n = 100000
        t = np.linspace(0, 1.0, n, endpoint=False)
        dt = t[1] - t[0]
        f_3dB = 10000.0
        signal = np.sin(2 * np.pi * 100.0 * t)
        out = apply_lowpass_fft(signal, dt, f_3dB)
        ratio = out.std() / signal.std()
        assert 0.999 < ratio < 1.001, f"Expected ~1, got {ratio:.4f}"

    def test_at_cutoff_amplitude_is_minus3dB(self):
        n = 100000
        t = np.linspace(0, 1.0, n, endpoint=False)
        dt = t[1] - t[0]
        f_3dB = 1000.0
        signal = np.sin(2 * np.pi * f_3dB * t)
        out = apply_lowpass_fft(signal, dt, f_3dB)
        ratio = out.std() / signal.std()
        assert 0.68 < ratio < 0.73, f"Expected ~0.707, got {ratio:.4f}"

    def test_well_above_cutoff_attenuated(self):
        n = 100000
        t = np.linspace(0, 1.0, n, endpoint=False)
        dt = t[1] - t[0]
        f_3dB = 1000.0
        # Use 10 kHz (10x above cutoff) to stay clear of Nyquist aliasing at 50 kHz
        signal = np.sin(2 * np.pi * 10000.0 * t)
        out = apply_lowpass_fft(signal, dt, f_3dB)
        ratio = out.std() / (signal.std() + 1e-20)
        # |H(10kHz)| = 1/sqrt(1+10^2) ≈ 0.0995 — below 0.15 with numerical margin
        assert ratio < 0.15, f"High-freq signal should be heavily attenuated, got {ratio:.4f}"

    def test_zero_cutoff_returns_copy(self):
        signal = np.sin(np.linspace(0, 1, 100))
        out = apply_lowpass_fft(signal, 0.01, f_3dB_hz=0.0)
        assert np.array_equal(out, signal)

    def test_short_signal_returns_copy(self):
        signal = np.array([1.0])
        out = apply_lowpass_fft(signal, 0.01, f_3dB_hz=1000.0)
        assert np.array_equal(out, signal)

    def test_output_length_preserved(self):
        signal = np.random.randn(500)
        out = apply_lowpass_fft(signal, 1e-4, 5000.0)
        assert len(out) == len(signal)


# ── apply_slew_limit ──────────────────────────────────────────────────────────

class TestSlewLimit:
    def test_step_is_clipped(self):
        signal = np.concatenate([np.zeros(500), np.full(500, 5000.0)])
        dt = 1e-6
        slew_max = 300e6
        out = apply_slew_limit(signal, dt, slew_max)
        diffs = np.abs(np.diff(out))
        assert np.all(diffs <= slew_max * dt * 1.001)

    def test_slow_ramp_is_unmodified(self):
        n = 1000
        dt = 1e-3
        slew_max = 1e6
        # ramp at 10 V/s, well below 1e6 V/s
        signal = np.linspace(0, 10.0, n)
        out = apply_slew_limit(signal, dt, slew_max)
        assert np.allclose(out, signal, atol=1e-9)

    def test_zero_slew_returns_copy(self):
        signal = np.random.randn(100)
        out = apply_slew_limit(signal, 1e-4, 0.0)
        assert np.array_equal(out, signal)

    def test_output_starts_at_input_start(self):
        signal = np.array([7.0, 7.0, 100.0, 100.0])
        out = apply_slew_limit(signal, 1e-3, 1.0)
        assert out[0] == 7.0

    def test_single_element_returns_unchanged(self):
        signal = np.array([3.0])
        out = apply_slew_limit(signal, 1e-4, 1e6)
        assert out[0] == 3.0


# ── apply_amplifier (full pipeline) ──────────────────────────────────────────

class TestApplyAmplifier:
    def _params(self, bw=10000.0, slew=300.0, kV_per_mm=0.368):
        return {
            "amplifier_bw_hz": bw,
            "amplifier_slew_V_per_us": slew,
            "kV_per_mm": kV_per_mm,
        }

    def test_dc_passes_through(self):
        t = np.linspace(0, 0.1, 10000, endpoint=False)
        dc = np.full_like(t, 5.0)
        x_out, _ = apply_amplifier(t, dc, dc * 0, self._params())
        assert np.allclose(x_out, dc, atol=0.05)

    def test_1kHz_amplitude_retained(self):
        n = 100000
        t = np.linspace(0, 0.1, n, endpoint=False)
        x = 5.0 * np.sin(2 * np.pi * 1000.0 * t)
        x_out, _ = apply_amplifier(t, x, np.zeros_like(x), self._params())
        ratio = x_out.std() / x.std()
        assert 0.99 < ratio < 1.01, f"1 kHz ratio={ratio:.4f}"

    def test_10kHz_at_minus3dB(self):
        n = 100000
        t = np.linspace(0, 0.1, n, endpoint=False)
        x = 5.0 * np.sin(2 * np.pi * 10000.0 * t)
        x_out, _ = apply_amplifier(t, x, np.zeros_like(x), self._params())
        ratio = x_out.std() / x.std()
        assert 0.68 < ratio < 0.73, f"10 kHz ratio={ratio:.4f}"

    def test_output_length_preserved(self):
        t = np.linspace(0, 0.01, 1000, endpoint=False)
        x = np.sin(2 * np.pi * 100.0 * t)
        x_out, y_out = apply_amplifier(t, x, x, self._params())
        assert len(x_out) == 1000
        assert len(y_out) == 1000

    def test_short_trajectory_returns_copy(self):
        t = np.array([0.0])
        x = np.array([3.0])
        x_out, y_out = apply_amplifier(t, x, x, self._params())
        assert x_out[0] == 3.0

    def test_slew_limit_in_full_pipeline(self):
        n = 10000
        t = np.linspace(0, 0.01, n, endpoint=False)
        x = 20.0 * np.sign(np.sin(2 * np.pi * 10000.0 * t))
        params = self._params(bw=100000.0, slew=1.0)  # 1 V/us slew is very tight
        x_out, _ = apply_amplifier(t, x, np.zeros_like(x), params)
        # Output amplitude should be severely limited
        assert x_out.std() < x.std() * 0.5


# ── required_slew_rate_V_per_s ────────────────────────────────────────────────

class TestRequiredSlewRate:
    def test_dc_signal_requires_zero_slew(self):
        t = np.linspace(0, 0.01, 1000)
        x = np.ones(1000) * 5.0
        assert required_slew_rate_V_per_s(x, t, 0.368) == 0.0

    def test_higher_frequency_requires_more_slew(self):
        t = np.linspace(0, 0.01, 10000, endpoint=False)
        x_slow = 5.0 * np.sin(2 * np.pi * 100.0 * t)
        x_fast = 5.0 * np.sin(2 * np.pi * 1000.0 * t)
        slew_slow = required_slew_rate_V_per_s(x_slow, t, 1.0)
        slew_fast = required_slew_rate_V_per_s(x_fast, t, 1.0)
        assert slew_fast > slew_slow

    def test_single_sample_returns_zero(self):
        assert required_slew_rate_V_per_s(np.array([1.0]), np.array([0.0]), 1.0) == 0.0


# ── deflection physics ────────────────────────────────────────────────────────

class TestDeflectionPhysics:
    def test_calculate_drive_returns_expected_keys(self):
        r = calculate_drive_for_deflection(
            deflection_mm=25.0,
            energy_MeV=3.0,
            charge_state=1,
            travel_mm=3000.0,
        )
        assert "plate_kV" in r
        assert "fg_peak_V" in r
        assert "fg_vpp_V" in r
        assert "exceeds_amplifier" in r
        assert "exceeds_fg" in r

    def test_larger_deflection_needs_more_voltage(self):
        r1 = calculate_drive_for_deflection(10.0, 3.0, 1, 3000.0)
        r2 = calculate_drive_for_deflection(30.0, 3.0, 1, 3000.0)
        assert r2["plate_kV"] > r1["plate_kV"]

    def test_higher_energy_needs_more_voltage(self):
        r1 = calculate_drive_for_deflection(25.0, 1.0, 1, 3000.0)
        r2 = calculate_drive_for_deflection(25.0, 10.0, 1, 3000.0)
        assert r2["plate_kV"] > r1["plate_kV"]

    def test_fg_vpp_is_twice_peak(self):
        r = calculate_drive_for_deflection(25.0, 3.0, 1, 3000.0)
        assert abs(r["fg_vpp_V"] - 2.0 * r["fg_peak_V"]) < 1e-9

    def test_small_deflection_within_limits(self):
        r = calculate_drive_for_deflection(1.0, 1.0, 1, 3000.0)
        assert not r["exceeds_amplifier"]
