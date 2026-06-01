import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "rbl"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "rbl", "gui"))

import pytest


def base_params():
    """Minimal valid params dict for scan pipeline tests."""
    return {
        "fwhm_x_mm": 2.0,
        "fwhm_y_mm": 2.0,
        "aperture_xL_mm": -15.0,
        "aperture_xR_mm":  15.0,
        "aperture_yB_mm": -15.0,
        "aperture_yT_mm":  15.0,
        "ax_mm": 20.0,
        "ay_mm": 20.0,
        "fx_hz": 1000.0,
        "fy_hz": 100.0,
        "pattern": "classic",
        "lissajous_phase_deg": 0.0,
        "T_total_ms": 50.0,
        "n_time_samples": 5000,
        "grid_nx": 64,
        "grid_ny": 64,
        "tau_recomb_ms": 1.0,
        "D_interstitial_m2s": 1e-15,
        "fdrt_threshold_hz": 500.0,
        "amplifier_bw_hz": 10000.0,
        "simulate_amplifier": False,
        "amplifier_slew_V_per_us": 300.0,
        "kV_per_mm": 0.02,
        "flatness_target_pct": 10.0,
        "w1": 1.0,
        "w2": 1.0,
        "w3": 1.0,
        "w4": 1.0,
        "w5": 1.0,
        "w6": 0.5,
    }


@pytest.fixture
def params():
    return base_params()
