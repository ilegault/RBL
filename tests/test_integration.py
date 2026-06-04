"""
End-to-end integration tests for the compute pipeline:
trajectory -> dose -> metrics, plus the amplifier model and optimizer objective.

These promote the key physics checks from rbl/config/validation.py into the
pytest suite so they run on every CI invocation.
"""
import numpy as np
import pytest

from rbl.config.defaults import DEFAULTS
from rbl.scan.patterns import get_pattern, get_realistic_trajectory
from rbl.scan.dose import compute_dose
from rbl.scan.metrics import (
    flatness_pct, pinch_metric, triangularity_score,
    _aperture_mask_from_edges, compute_all_metrics,
)


def _params(**over):
    p = dict(DEFAULTS)
    p.update(over)
    return p


# ── Dose pipeline ────────────────────────────────────────────────────────────

class TestDosePipeline:
    def test_dwell_integral_equals_total_time(self):
        p = _params(aperture_xL_mm=-30, aperture_xR_mm=30,
                    aperture_yB_mm=-30, aperture_yT_mm=30,
                    grid_nx=128, grid_ny=128,
                    T_total_ms=200.0, n_time_samples=40000,
                    simulate_amplifier=False)
        t, x, y = get_realistic_trajectory(p)
        _, rho, _, _ = compute_dose(p, t, x, y)
        T_total_s = p["T_total_ms"] * 1e-3
        rel_err = abs(rho.sum() - T_total_s) / T_total_s
        assert rel_err < 0.02, f"dwell integral off by {rel_err*100:.2f}%"

    def test_dose_is_nonnegative_and_finite(self):
        p = _params(grid_nx=96, grid_ny=96, T_total_ms=100.0, n_time_samples=20000)
        t, x, y = get_realistic_trajectory(p)
        dose, _, _, _ = compute_dose(p, t, x, y)
        assert np.all(np.isfinite(dose))
        assert np.all(dose >= 0.0)

    def test_dose_zero_outside_aperture(self):
        p = _params(grid_nx=96, grid_ny=96, T_total_ms=100.0, n_time_samples=20000)
        t, x, y = get_realistic_trajectory(p)
        dose, _, xe, ye = compute_dose(p, t, x, y)
        mask = _aperture_mask_from_edges(xe, ye, p["aperture_xL_mm"],
                                         p["aperture_xR_mm"], p["aperture_yB_mm"],
                                         p["aperture_yT_mm"])
        assert dose[~mask].sum() == 0.0

    def test_default_mibl_flatness_not_broken(self):
        p = _params(simulate_amplifier=False)
        t, x, y = get_realistic_trajectory(p)
        dose, _, xe, ye = compute_dose(p, t, x, y)
        mask = _aperture_mask_from_edges(xe, ye, p["aperture_xL_mm"],
                                         p["aperture_xR_mm"], p["aperture_yB_mm"],
                                         p["aperture_yT_mm"])
        flat = flatness_pct(dose[mask])
        assert np.isfinite(flat) and flat < 50.0


class TestLissajousCoverage:
    def test_all_aperture_pixels_get_dose(self):
        p = _params(fx_hz=5450.0, fy_hz=6700.0, ax_mm=8.0, ay_mm=10.0,
                    aperture_xL_mm=-7, aperture_xR_mm=7,
                    aperture_yB_mm=-9, aperture_yT_mm=9,
                    fwhm_x_mm=1.0, fwhm_y_mm=1.0,
                    T_total_ms=300.0, n_time_samples=120000,
                    grid_nx=96, grid_ny=96, pattern="lissajous",
                    simulate_amplifier=False)
        t, x, y = get_realistic_trajectory(p)
        dose, _, xe, ye = compute_dose(p, t, x, y)
        mask = _aperture_mask_from_edges(xe, ye, p["aperture_xL_mm"],
                                         p["aperture_xR_mm"], p["aperture_yB_mm"],
                                         p["aperture_yT_mm"])
        assert dose[mask].min() > 0.0


# ── Amplifier behaviour through the trajectory pipeline ──────────────────────

class TestAmplifierInPipeline:
    def test_toggle_off_matches_ideal_pattern_exactly(self):
        p = _params(simulate_amplifier=False)
        t_legacy, x_legacy, y_legacy = get_pattern(p["pattern"], p)
        t_new, x_new, y_new = get_realistic_trajectory(p)
        assert np.allclose(x_legacy, x_new)
        assert np.allclose(y_legacy, y_new)

    def test_low_freq_passes_nearly_unchanged(self):
        p = _params(fx_hz=500.0, fy_hz=50.0, T_total_ms=100.0,
                    n_time_samples=50000, simulate_amplifier=False)
        _, x_ideal, _ = get_realistic_trajectory(p)
        p["simulate_amplifier"] = True
        _, x_real, _ = get_realistic_trajectory(p)
        rel = np.max(np.abs(x_ideal - x_real)) / (np.max(np.abs(x_ideal)) + 1e-12)
        assert rel < 0.05

    def test_high_freq_rounds_waveform(self):
        p = _params(fx_hz=15000.0, fy_hz=50.0, T_total_ms=30.0,
                    n_time_samples=100000, simulate_amplifier=False)
        t, x_ideal, _ = get_realistic_trajectory(p)
        p["simulate_amplifier"] = True
        _, x_real, _ = get_realistic_trajectory(p)
        tri_ideal = triangularity_score(x_ideal, t, 15000.0)
        tri_real = triangularity_score(x_real, t, 15000.0)
        assert tri_real < tri_ideal - 0.02


# ── Patterns: shape / bounds for every pattern ───────────────────────────────

class TestAllPatterns:
    @pytest.mark.parametrize("pattern",
                             ["classic", "alt_axes", "lissajous", "spiral",
                              "sinusoidal", "wobble"])
    def test_trajectory_finite_and_bounded(self, pattern):
        p = _params(pattern=pattern, T_total_ms=50.0, n_time_samples=10000,
                    simulate_amplifier=False)
        t, x, y = get_realistic_trajectory(p)
        assert len(t) == len(x) == len(y) == 10000
        assert np.all(np.isfinite(x)) and np.all(np.isfinite(y))
        # amplitudes never exceed the configured scan amplitude (+small tol)
        assert np.max(np.abs(x)) <= max(p["ax_mm"], p["ay_mm"]) * 1.001 + 1e-9
        assert np.max(np.abs(y)) <= max(p["ax_mm"], p["ay_mm"]) * 1.001 + 1e-9

    @pytest.mark.parametrize("pattern",
                             ["classic", "lissajous", "sinusoidal", "wobble"])
    def test_metrics_finite_for_each_pattern(self, pattern):
        p = _params(pattern=pattern, grid_nx=64, grid_ny=64,
                    T_total_ms=80.0, n_time_samples=16000)
        t, x, y = get_realistic_trajectory(p)
        dose, rho, xe, ye = compute_dose(p, t, x, y)
        m = compute_all_metrics(dose, rho, x, y, t[1] - t[0], xe, ye, p)
        assert np.isfinite(m["flatness_pct"])
        assert np.isfinite(m["pinch_pct"])


# ── Optimizer objective (FDRT physics) ───────────────────────────────────────

class TestOptimizerObjective:
    def test_slow_fy_is_penalised(self):
        from rbl.scan.optimizer import objective
        p = _params(simulate_amplifier=True)
        # A pathologically slow slow-axis (fy=14 Hz) must score worse than a
        # sensible operating point.
        J_bad = objective([579.0, 14.0, 1.471, 1.448], p)
        J_good = objective([2000.0, 600.0, 1.3, 1.3], p)
        assert J_bad > J_good

    def test_objective_returns_finite_scalar(self):
        from rbl.scan.optimizer import objective
        p = _params(simulate_amplifier=True)
        J = objective([2000.0, 500.0, 1.3, 1.3], p)
        assert np.isfinite(J)
        assert isinstance(J, float)

    def test_objective_handles_bad_input_gracefully(self):
        from rbl.scan.optimizer import objective
        p = _params()
        # zero frequencies shouldn't crash the optimizer; it returns a big cost
        J = objective([0.0, 0.0, 1.3, 1.3], p)
        assert np.isfinite(J)
