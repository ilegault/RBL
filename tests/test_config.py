"""Tests for rbl.config — defaults and lab presets."""
import pytest

from rbl.config.defaults import DEFAULTS
from rbl.config.lab_presets import FREQUENCY_PRESETS


class TestDefaults:
    def test_required_keys_present(self):
        required = [
            "fwhm_x_mm", "fwhm_y_mm",
            "aperture_xL_mm", "aperture_xR_mm", "aperture_yB_mm", "aperture_yT_mm",
            "ax_mm", "ay_mm",
            "fx_hz", "fy_hz",
            "pattern",
            "T_total_ms", "n_time_samples",
            "grid_nx", "grid_ny",
            "tau_recomb_ms",
            "fdrt_threshold_hz",
            "amplifier_bw_hz",
            "simulate_amplifier",
            "amplifier_slew_V_per_us",
            "kV_per_mm",
        ]
        for key in required:
            assert key in DEFAULTS, f"DEFAULTS missing key: {key}"

    def test_fwhm_positive(self):
        assert DEFAULTS["fwhm_x_mm"] > 0
        assert DEFAULTS["fwhm_y_mm"] > 0

    def test_aperture_symmetric_or_valid(self):
        assert DEFAULTS["aperture_xL_mm"] < DEFAULTS["aperture_xR_mm"]
        assert DEFAULTS["aperture_yB_mm"] < DEFAULTS["aperture_yT_mm"]

    def test_frequencies_positive(self):
        assert DEFAULTS["fx_hz"] > 0
        assert DEFAULTS["fy_hz"] > 0

    def test_amplifier_bandwidth_positive(self):
        assert DEFAULTS["amplifier_bw_hz"] > 0

    def test_grid_sizes_positive_integers(self):
        assert isinstance(DEFAULTS["grid_nx"], int)
        assert isinstance(DEFAULTS["grid_ny"], int)
        assert DEFAULTS["grid_nx"] > 0
        assert DEFAULTS["grid_ny"] > 0

    def test_scan_duration_positive(self):
        assert DEFAULTS["T_total_ms"] > 0

    def test_time_samples_positive(self):
        assert DEFAULTS["n_time_samples"] > 0

    def test_pattern_is_valid_string(self):
        valid = {"classic", "alt_axes", "lissajous", "spiral", "sinusoidal", "wobble"}
        assert DEFAULTS["pattern"] in valid

    def test_tau_recomb_positive(self):
        assert DEFAULTS["tau_recomb_ms"] > 0

    def test_fdrt_threshold_positive(self):
        assert DEFAULTS["fdrt_threshold_hz"] > 0

    def test_slew_rate_positive(self):
        assert DEFAULTS["amplifier_slew_V_per_us"] > 0

    def test_calibration_positive(self):
        assert DEFAULTS["kV_per_mm"] > 0

    def test_simulate_amplifier_is_bool(self):
        assert isinstance(DEFAULTS["simulate_amplifier"], bool)

    def test_amplitudes_positive(self):
        assert DEFAULTS["ax_mm"] > 0
        assert DEFAULTS["ay_mm"] > 0


class TestFrequencyPresets:
    def test_presets_not_empty(self):
        assert len(FREQUENCY_PRESETS) > 0

    def test_each_preset_has_f1_and_f2(self):
        for name, preset in FREQUENCY_PRESETS.items():
            assert "f1_hz" in preset, f"Preset {name!r} missing f1_hz"
            assert "f2_hz" in preset, f"Preset {name!r} missing f2_hz"

    def test_frequencies_positive(self):
        for name, preset in FREQUENCY_PRESETS.items():
            assert preset["f1_hz"] > 0, f"Preset {name!r} f1_hz <= 0"
            assert preset["f2_hz"] > 0, f"Preset {name!r} f2_hz <= 0"

    def test_known_labs_present(self):
        # At least one known lab should exist (case-insensitive substring match)
        names_lower = [k.lower() for k in FREQUENCY_PRESETS]
        known = ["michigan", "ibl", "oxford", "jefferson"]
        found = any(any(lab in n for n in names_lower) for lab in known)
        assert found, f"No known lab found in presets: {list(FREQUENCY_PRESETS)}"
