"""
test_raster_plan.py
Unit tests for rbl/hardware/raster_plan.py.

These pin the output values of the extracted pure functions against values
produced by the original widget implementation before the refactor, so a
regression would be immediately visible.
"""
import math

import pytest

from rbl.config.calibration_config import CAL_MAX_KV
from rbl.config.raster_defaults import AMP_MAX_BANDWIDTH_HZ
from rbl.config.steerer_geometry import PLATE_GAP_CM, PLATE_LENGTH_CM
from rbl.hardware.raster_plan import envelope_status, steerer_limited_solve

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def base_sol():
    """Centred 20 mm sweep, 3 MeV proton — the reference case."""
    return steerer_limited_solve(
        width_mm=20.0, centre_mm=0.0, fwhm_mm=5.0, k=1.0,
        drift_to_slit_mm=500.0, drift_to_sample_mm=800.0,
        l_cm=PLATE_LENGTH_CM, d_cm=PLATE_GAP_CM,
        charge=1, energy_ev=3e6,
    )


@pytest.fixture
def offset_sol():
    """Off-centre 20 mm sweep (5 mm offset) — tests asymmetric blades."""
    return steerer_limited_solve(
        width_mm=20.0, centre_mm=5.0, fwhm_mm=5.0, k=1.0,
        drift_to_slit_mm=500.0, drift_to_sample_mm=800.0,
        l_cm=PLATE_LENGTH_CM, d_cm=PLATE_GAP_CM,
        charge=1, energy_ev=3e6,
    )


@pytest.fixture
def caps_500pf():
    return {"X+": (500.0, "measured"), "X-": (500.0, "measured"),
            "Y+": (500.0, "measured"), "Y-": (500.0, "measured")}


@pytest.fixture
def nominal_solution():
    """Minimal solution dict for envelope_status (only peak_plate_kv used)."""
    return {"X": {"peak_plate_kv": 1.0}, "Y": {"peak_plate_kv": 1.0}}


# ---------------------------------------------------------------------------
# TestSteererLimitedSolve — required keys
# ---------------------------------------------------------------------------

class TestSteererLimitedSolveKeys:
    REQUIRED = [
        "amplitude_kv", "offset_kv", "ac_plate_kv", "peak_plate_kv",
        "scan_half_span_mm", "scan_min_mm", "scan_max_mm", "sample_half_mm",
        "blade_plus_mm", "blade_minus_mm",
        "mm_per_kv_at_slit", "magnification",
        "sweep_half_at_slit_mm", "sweep_center_at_slit_mm",
        "sweep_half_at_target_mm", "painted_full_mm", "painted_center_mm",
        "dose_uniformity_pct", "dose_transmitted_fraction",
        "dose_regime", "dose_droop_plus_pct", "dose_droop_minus_pct",
        "dose_x_mm", "dose_dose", "fwhm_at_slit_mm",
    ]

    def test_all_required_keys_present(self, base_sol):
        for key in self.REQUIRED:
            assert key in base_sol, f"missing key: {key}"

    def test_dose_regime_label(self, base_sol):
        assert base_sol["dose_regime"] == "steerer-limited"

    def test_painted_full_echoes_input(self, base_sol):
        assert base_sol["painted_full_mm"] == 20.0

    def test_painted_center_echoes_input(self, base_sol):
        assert base_sol["painted_center_mm"] == 0.0

    def test_fwhm_at_slit_echoes_input(self, base_sol):
        assert base_sol["fwhm_at_slit_mm"] == 5.0

    def test_nan_fields_for_steerer_mode(self, base_sol):
        """dose_transmitted_fraction and droop are NaN in steerer-limited mode."""
        assert math.isnan(base_sol["dose_transmitted_fraction"])
        assert math.isnan(base_sol["dose_droop_plus_pct"])
        assert math.isnan(base_sol["dose_droop_minus_pct"])


# ---------------------------------------------------------------------------
# TestSteererLimitedSolve — pinned values (centred sweep)
# ---------------------------------------------------------------------------

class TestSteererLimitedSolvePinned:
    """Pin against values captured from the original widget implementation."""

    def test_amplitude_kv(self, base_sol):
        assert abs(base_sol["amplitude_kv"] - 34.2) < 1e-6

    def test_offset_kv_zero_for_centred(self, base_sol):
        assert abs(base_sol["offset_kv"]) < 1e-9

    def test_blade_plus_equals_blade_minus_centred(self, base_sol):
        assert abs(base_sol["blade_plus_mm"] - base_sol["blade_minus_mm"]) < 1e-9

    def test_blade_plus_pinned(self, base_sol):
        assert abs(base_sol["blade_plus_mm"] - 9.375) < 1e-6

    def test_sweep_half_at_target_pinned(self, base_sol):
        assert abs(base_sol["sweep_half_at_target_mm"] - 15.0) < 1e-6

    def test_magnification_pinned(self, base_sol):
        # 800 mm / 500 mm = 1.6
        assert abs(base_sol["magnification"] - 1.6) < 1e-9

    def test_dose_uniformity_positive(self, base_sol):
        assert base_sol["dose_uniformity_pct"] > 0

    def test_dose_profile_arrays_nonempty(self, base_sol):
        assert base_sol["dose_x_mm"].size > 0
        assert base_sol["dose_dose"].size > 0

    def test_dose_dose_normalised(self, base_sol):
        """Dose profile is normalised to a maximum of 1.0."""
        import numpy as np
        assert abs(float(np.max(base_sol["dose_dose"])) - 1.0) < 1e-9


# ---------------------------------------------------------------------------
# TestSteererLimitedSolve — offset sweep
# ---------------------------------------------------------------------------

class TestSteererLimitedSolveOffset:
    def test_blade_plus_gt_blade_minus_for_positive_offset(self, offset_sol):
        """Positive centre offset → plus blade must open more than minus."""
        assert offset_sol["blade_plus_mm"] > offset_sol["blade_minus_mm"]

    def test_blade_plus_pinned(self, offset_sol):
        assert abs(offset_sol["blade_plus_mm"] - 12.5) < 1e-6

    def test_blade_minus_pinned(self, offset_sol):
        assert abs(offset_sol["blade_minus_mm"] - 6.25) < 1e-6

    def test_painted_center_echoes_input(self, offset_sol):
        assert offset_sol["painted_center_mm"] == 5.0

    def test_amplitude_same_as_centred(self, base_sol, offset_sol):
        """Amplitude depends on width, not centre — should be the same."""
        assert abs(base_sol["amplitude_kv"] - offset_sol["amplitude_kv"]) < 1e-6


# ---------------------------------------------------------------------------
# TestSteererLimitedSolve — parameter sensitivity
# ---------------------------------------------------------------------------

class TestSteererLimitedSolveSensitivity:
    def test_wider_patch_needs_more_voltage(self):
        common = dict(centre_mm=0.0, fwhm_mm=5.0, k=1.0,
                      drift_to_slit_mm=500.0, drift_to_sample_mm=800.0,
                      l_cm=PLATE_LENGTH_CM, d_cm=PLATE_GAP_CM,
                      charge=1, energy_ev=3e6)
        narrow = steerer_limited_solve(width_mm=10.0, **common)
        wide   = steerer_limited_solve(width_mm=30.0, **common)
        assert wide["amplitude_kv"] > narrow["amplitude_kv"]

    def test_heavier_species_needs_less_voltage(self):
        """Higher energy → harder to deflect → more voltage for same patch."""
        common = dict(width_mm=20.0, centre_mm=0.0, fwhm_mm=5.0, k=1.0,
                      drift_to_slit_mm=500.0, drift_to_sample_mm=800.0,
                      l_cm=PLATE_LENGTH_CM, d_cm=PLATE_GAP_CM, charge=1)
        low_e  = steerer_limited_solve(energy_ev=1e6, **common)
        high_e = steerer_limited_solve(energy_ev=5e6, **common)
        assert high_e["amplitude_kv"] > low_e["amplitude_kv"]

    def test_longer_drift_increases_slit_sweep(self):
        """Longer drift to sample → beam sweeps further at the slit for same plate kV."""
        common = dict(width_mm=20.0, centre_mm=0.0, fwhm_mm=5.0, k=1.0,
                      drift_to_slit_mm=500.0,
                      l_cm=PLATE_LENGTH_CM, d_cm=PLATE_GAP_CM,
                      charge=1, energy_ev=3e6)
        short = steerer_limited_solve(drift_to_sample_mm=600.0, **common)
        long_ = steerer_limited_solve(drift_to_sample_mm=1000.0, **common)
        assert long_["magnification"] > short["magnification"]


# ---------------------------------------------------------------------------
# TestEnvelopeStatus — structure
# ---------------------------------------------------------------------------

class TestEnvelopeStatusKeys:
    REQUIRED = ["in_envelope", "worst_label", "ratio", "kv", "freq_hz",
                "env_kv", "walls", "over_ceiling", "exceeded_bandwidth"]

    def test_all_keys_present(self, caps_500pf, nominal_solution):
        st = envelope_status(caps_500pf, {"X": 1.0, "Y": 1.0},
                             {"X": 10.0, "Y": 10.0}, nominal_solution)
        for key in self.REQUIRED:
            assert key in st, f"missing key: {key}"

    def test_walls_has_freq_and_envelope_keys(self, caps_500pf, nominal_solution):
        st = envelope_status(caps_500pf, {"X": 1.0, "Y": 1.0},
                             {"X": 10.0, "Y": 10.0}, nominal_solution)
        assert "freq_hz"      in st["walls"]
        assert "envelope_kv"  in st["walls"]
        assert "current_wall_kv" in st["walls"]
        assert "voltage_wall_kv" in st["walls"]


# ---------------------------------------------------------------------------
# TestEnvelopeStatus — inside / outside / bandwidth
# ---------------------------------------------------------------------------

class TestEnvelopeStatusDecisions:
    def test_low_kv_low_freq_is_inside(self, caps_500pf, nominal_solution):
        st = envelope_status(caps_500pf, {"X": 0.5, "Y": 0.5},
                             {"X": 1.0, "Y": 1.0}, nominal_solution)
        assert st["in_envelope"] is True
        assert st["over_ceiling"] == []
        assert st["exceeded_bandwidth"] is False

    def test_very_high_kv_is_outside(self, caps_500pf):
        # peak_plate_kv also high — triggers the ceiling check
        sol_high = {"X": {"peak_plate_kv": 6.0}, "Y": {"peak_plate_kv": 6.0}}
        st = envelope_status(caps_500pf, {"X": 6.0, "Y": 6.0},
                             {"X": 1.0, "Y": 1.0}, sol_high)
        assert st["in_envelope"] is False

    def test_freq_above_bandwidth_not_in_envelope(self, caps_500pf, nominal_solution):
        freq_too_high = AMP_MAX_BANDWIDTH_HZ * 2
        st = envelope_status(caps_500pf, {"X": 1.0, "Y": 1.0},
                             {"X": freq_too_high, "Y": freq_too_high},
                             nominal_solution)
        assert st["in_envelope"] is False
        assert st["exceeded_bandwidth"] is True
        assert math.isnan(st["env_kv"])

    def test_over_ceiling_axis_listed(self, caps_500pf):
        # X peak exceeds CAL_MAX_KV, Y does not
        sol_mixed = {"X": {"peak_plate_kv": CAL_MAX_KV + 0.1},
                     "Y": {"peak_plate_kv": 1.0}}
        st = envelope_status(caps_500pf, {"X": 1.0, "Y": 1.0},
                             {"X": 1.0, "Y": 1.0}, sol_mixed)
        assert "X" in st["over_ceiling"]
        assert "Y" not in st["over_ceiling"]
        assert st["in_envelope"] is False

    def test_ratio_reflects_worst_channel(self, nominal_solution):
        # At 1000 Hz, 5000 pF current wall ≈ 1.0 kV while 100 pF stays at 5 kV.
        # So Y channels (5000 pF) are tighter and should be the worst.
        caps_asymm = {"X+": (100.0, "fallback"), "X-": (100.0, "fallback"),
                      "Y+": (5000.0, "fallback"), "Y-": (5000.0, "fallback")}
        st = envelope_status(caps_asymm, {"X": 1.0, "Y": 1.0},
                             {"X": 1000.0, "Y": 1000.0}, nominal_solution)
        assert st["worst_label"] in ("Y+", "Y-"), (
            f"expected Y channel to be worst (tightest envelope), got {st['worst_label']}")
        # X ratio should be much smaller than Y ratio
        assert st["ratio"] >= 0.9  # Y is at or near its envelope wall at 1 kV / ~1 kV wall


    def test_ratio_monotone_with_kv(self, caps_500pf, nominal_solution):
        """Higher requested kV → higher ratio (closer to or past the wall)."""
        st_lo = envelope_status(caps_500pf, {"X": 0.5, "Y": 0.5},
                                {"X": 1.0, "Y": 1.0}, nominal_solution)
        st_hi = envelope_status(caps_500pf, {"X": 2.0, "Y": 2.0},
                                {"X": 1.0, "Y": 1.0}, nominal_solution)
        assert st_hi["ratio"] > st_lo["ratio"]

    def test_custom_axis_of_channel(self, nominal_solution):
        """Non-standard axis mapping is respected."""
        caps = {"A": (500.0, "measured"), "B": (500.0, "measured"),
                "C": (500.0, "measured"), "D": (500.0, "measured")}
        aoc  = {"A": "X", "B": "X", "C": "Y", "D": "Y"}
        st = envelope_status(caps, {"X": 1.0, "Y": 1.0},
                             {"X": 1.0, "Y": 1.0}, nominal_solution,
                             axis_of_channel=aoc)
        assert st["worst_label"] in ("A", "B", "C", "D")
