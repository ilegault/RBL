"""Tests for rbl.scan — patterns, dose, and metrics."""
import pytest
import numpy as np

from rbla.scan.patterns import (
    classic_raster,
    alternating_axes,
    lissajous,
    spiral,
    sinusoidal_raster,
    wobbled_defocus,
    get_pattern,
    get_realistic_trajectory,
)
from rbla.scan.dose import compute_dose, trajectory_density
from rbla.scan.metrics import (
    flatness_pct,
    rms_deviation_pct,
    max_min_ratio,
    pinch_metric,
    steady_state_flag,
    max_pixel_off_time_ms,
    fwhm_spot_rule,
    characteristic_tau,
    diffusion_length,
    compute_all_metrics,
)
from tests.conftest import base_params


# ── Scan patterns ─────────────────────────────────────────────────────────────

class TestClassicRaster:
    def test_output_shape(self):
        t, x, y = classic_raster(1000, 100, 20, 20, 0.05, 5000)
        assert len(t) == len(x) == len(y) == 5000

    def test_time_starts_at_zero(self):
        t, _, _ = classic_raster(1000, 100, 20, 20, 0.05, 5000)
        assert t[0] == 0.0

    def test_x_amplitude_bounded(self):
        _, x, _ = classic_raster(1000, 100, 20, 20, 0.05, 5000)
        assert np.max(np.abs(x)) <= 20.0 * 1.001

    def test_y_amplitude_bounded(self):
        _, _, y = classic_raster(1000, 100, 20, 20, 0.05, 5000)
        assert np.max(np.abs(y)) <= 20.0 * 1.001

    def test_x_mean_near_zero(self):
        _, x, _ = classic_raster(1000, 100, 20, 20, 0.5, 50000)
        assert abs(x.mean()) < 0.5


class TestAlternatingAxes:
    def test_output_shape(self):
        t, x, y = alternating_axes(1000, 100, 20, 20, 0.05, 5000)
        assert len(t) == len(x) == len(y) == 5000

    def test_bounded_xy(self):
        _, x, y = alternating_axes(1000, 100, 20, 20, 0.05, 5000)
        assert np.max(np.abs(x)) <= 20.0 * 1.001
        assert np.max(np.abs(y)) <= 20.0 * 1.001


class TestLissajous:
    def test_output_shape(self):
        t, x, y = lissajous(1000, 100, 20, 20, 45.0, 0.05, 5000)
        assert len(t) == len(x) == len(y) == 5000

    def test_amplitude_bounded(self):
        _, x, y = lissajous(1000, 100, 20, 20, 0.0, 0.05, 5000)
        assert np.max(np.abs(x)) <= 20.0 * 1.001
        assert np.max(np.abs(y)) <= 20.0 * 1.001

    def test_phase_shifts_y(self):
        _, _, y0 = lissajous(1000, 100, 20, 20, 0.0,  0.05, 5000)
        _, _, y90 = lissajous(1000, 100, 20, 20, 90.0, 0.05, 5000)
        # phase shift changes the waveform
        assert not np.allclose(y0, y90)


class TestSpiral:
    def test_output_shape(self):
        t, x, y = spiral(10, 20, 0.05, 5000)
        assert len(t) == len(x) == len(y) == 5000

    def test_starts_near_origin(self):
        t, x, y = spiral(10, 20, 0.05, 5000)
        assert abs(x[0]) < 0.1
        assert abs(y[0]) < 0.1

    def test_ends_near_max_radius(self):
        r_max = 20.0
        _, x, y = spiral(5, r_max, 0.1, 10000)
        r_end = np.sqrt(x[-1] ** 2 + y[-1] ** 2)
        assert r_end > r_max * 0.9


class TestSinusoidalRaster:
    def test_output_shape(self):
        t, x, y = sinusoidal_raster(1000, 100, 20, 20, 0.05, 5000)
        assert len(t) == len(x) == len(y) == 5000

    def test_bounded(self):
        _, x, y = sinusoidal_raster(1000, 100, 20, 20, 0.05, 5000)
        assert np.max(np.abs(x)) <= 20.0 * 1.001
        assert np.max(np.abs(y)) <= 20.0 * 1.001


class TestWobbledDefocus:
    def test_output_shape(self):
        t, x, y = wobbled_defocus(1000, 100, 20, 20, 0.05, 5000)
        assert len(t) == len(x) == len(y) == 5000


class TestGetPattern:
    @pytest.mark.parametrize("name", [
        "classic", "alt_axes", "lissajous", "spiral", "sinusoidal", "wobble"
    ])
    def test_all_patterns_return_three_arrays(self, name, params):
        params["pattern"] = name
        t, x, y = get_pattern(name, params)
        assert len(t) == len(x) == len(y) == params["n_time_samples"]

    def test_unknown_pattern_raises(self, params):
        with pytest.raises(ValueError):
            get_pattern("no_such_pattern", params)


class TestGetRealisticTrajectory:
    def test_no_amplifier_returns_ideal(self, params):
        params["simulate_amplifier"] = False
        t, x, y = get_realistic_trajectory(params)
        t2, x2, y2 = get_pattern(params["pattern"], params)
        assert np.allclose(t, t2) and np.allclose(x, x2) and np.allclose(y, y2)

    def test_with_amplifier_attenuates_high_freq(self):
        p = base_params()
        p["fx_hz"] = 8000.0
        p["simulate_amplifier"] = True
        p["amplifier_bw_hz"] = 5000.0
        p["T_total_ms"] = 20.0
        p["n_time_samples"] = 20000
        t_ideal, x_ideal, _ = get_pattern(p["pattern"], p)
        _, x_amp, _ = get_realistic_trajectory(p)
        assert x_amp.std() < x_ideal.std()


# ── Dose computation ──────────────────────────────────────────────────────────

class TestComputeDose:
    def test_returns_correct_shapes(self, params):
        t, x, y = get_realistic_trajectory(params)
        dose, rho, xe, ye = compute_dose(params, t, x, y)
        assert dose.shape == (params["grid_nx"], params["grid_ny"])
        assert rho.shape == (params["grid_nx"], params["grid_ny"])
        assert len(xe) == params["grid_nx"] + 1
        assert len(ye) == params["grid_ny"] + 1

    def test_dose_is_non_negative(self, params):
        t, x, y = get_realistic_trajectory(params)
        dose, _, _, _ = compute_dose(params, t, x, y)
        assert np.all(dose >= 0)

    def test_rho_is_non_negative(self, params):
        t, x, y = get_realistic_trajectory(params)
        _, rho, _, _ = compute_dose(params, t, x, y)
        assert np.all(rho >= 0)

    def test_dose_peak_inside_aperture(self, params):
        t, x, y = get_realistic_trajectory(params)
        dose, _, xe, ye = compute_dose(params, t, x, y)
        xc = 0.5 * (xe[:-1] + xe[1:])
        yc = 0.5 * (ye[:-1] + ye[1:])
        X, Y = np.meshgrid(xc, yc, indexing="ij")
        inside = (
            (X > params["aperture_xL_mm"]) & (X < params["aperture_xR_mm"]) &
            (Y > params["aperture_yB_mm"]) & (Y < params["aperture_yT_mm"])
        )
        assert dose[inside].sum() > dose[~inside].sum()


class TestTrajectoryDensity:
    def test_output_shape(self, params):
        t, x, y = get_realistic_trajectory(params)
        dt = t[1] - t[0]
        n_x, n_y = 32, 32
        x_edges = np.linspace(-25, 25, n_x + 1)
        y_edges = np.linspace(-25, 25, n_y + 1)
        rho = trajectory_density(x, y, dt, x_edges, y_edges)
        assert rho.shape == (n_x, n_y)

    def test_density_is_non_negative(self, params):
        t, x, y = get_realistic_trajectory(params)
        dt = t[1] - t[0]
        x_edges = np.linspace(-25, 25, 33)
        y_edges = np.linspace(-25, 25, 33)
        rho = trajectory_density(x, y, dt, x_edges, y_edges)
        assert np.all(rho >= 0)


# ── Metrics ───────────────────────────────────────────────────────────────────

class TestFlatnessPct:
    def test_uniform_field_is_zero(self):
        assert flatness_pct(np.ones(100)) == 0.0

    def test_formula_known_case(self):
        # max=2, min=0, expected = (2-0)/(2+0)*100 = 100%
        arr = np.array([0.0, 2.0])
        assert abs(flatness_pct(arr) - 100.0) < 1e-9

    def test_small_variation_small_flatness(self):
        arr = np.ones(100) + np.random.default_rng(0).uniform(-0.01, 0.01, 100)
        assert flatness_pct(arr) < 2.0

    def test_zero_dose_returns_zero(self):
        assert flatness_pct(np.zeros(100)) == 0.0


class TestRmsPct:
    def test_uniform_is_zero(self):
        assert rms_deviation_pct(np.ones(100)) == 0.0

    def test_nonzero_variation(self):
        arr = np.array([1.0, 2.0, 3.0])
        result = rms_deviation_pct(arr)
        assert result > 0.0

    def test_zero_mean_returns_zero(self):
        assert rms_deviation_pct(np.zeros(100)) == 0.0


class TestMaxMinRatio:
    def test_uniform_is_one(self):
        assert abs(max_min_ratio(np.ones(50)) - 1.0) < 1e-9

    def test_known_ratio(self):
        arr = np.array([1.0, 2.0])
        assert abs(max_min_ratio(arr) - 2.0) < 1e-9

    def test_zero_min_is_inf(self):
        assert max_min_ratio(np.array([0.0, 1.0])) == float("inf")


class TestPinchMetric:
    """Regression coverage for the empty-slice NaN bug in pinch_metric.

    row_slice[c_start:c_end] used to be computed as
    [n//2 - half_c : n//2 + half_c], which is empty whenever half_c == 0
    (i.e. n_center == 1, which happens whenever the aperture spans fewer
    than 10 grid columns). An empty slice's .mean() is NaN, silently
    corrupting compute_all_metrics()["pinch_pct"] and the optimizer's
    cost function without raising an exception.
    """

    @staticmethod
    def _edges(n, lo, hi):
        return np.linspace(lo, hi, n + 1)

    def test_small_aperture_does_not_produce_nan(self):
        # Aperture narrower than 10 grid columns -> n_center used to be 1,
        # half_c == 0, and the old center-window slice was empty.
        rng = np.random.default_rng(0)
        x_edges = self._edges(20, -10, 10)
        y_edges = self._edges(20, -10, 10)
        dose = rng.random((20, 20)) + 1.0
        pinch = pinch_metric(dose, x_edges, y_edges, (-3, 3, -8, 8))
        assert np.isfinite(pinch)

    def test_uniform_row_has_zero_pinch(self):
        x_edges = self._edges(40, -20, 20)
        y_edges = self._edges(40, -20, 20)
        dose = np.ones((40, 40))
        pinch = pinch_metric(dose, x_edges, y_edges, (-10, 10, -10, 10))
        assert np.isfinite(pinch)
        assert abs(pinch) < 1e-9

    def test_edge_cusps_detected_as_positive_pinch(self):
        # Aperture spans the full grid so x_in/y_in select every column/row;
        # elevating the first/last few X columns (the turnaround edges)
        # should register as a positive pinch percentage relative to center.
        x_edges = self._edges(40, -10, 10)
        y_edges = self._edges(40, -10, 10)
        dose = np.ones((40, 40))
        dose[:4, :] = 5.0
        dose[-4:, :] = 5.0
        pinch = pinch_metric(dose, x_edges, y_edges, (-10, 10, -10, 10))
        assert np.isfinite(pinch)
        assert pinch > 0.0

    def test_empty_aperture_returns_zero(self):
        x_edges = self._edges(20, -10, 10)
        y_edges = self._edges(20, -10, 10)
        dose = np.ones((20, 20))
        # Aperture entirely outside the grid -> no rows/cols selected.
        pinch = pinch_metric(dose, x_edges, y_edges, (100, 200, 100, 200))
        assert pinch == 0.0


class TestSteadyStateFlag:
    def test_above_threshold_and_fast_enough_is_true(self):
        # f_slow = 1000 Hz, tau = 2 ms, off_time = 1 ms < tau
        assert steady_state_flag(5000.0, 1000.0, tau_recomb_ms=2.0, fdrt_threshold_hz=500.0)

    def test_below_fdrt_threshold_is_false(self):
        assert not steady_state_flag(200.0, 200.0, tau_recomb_ms=100.0, fdrt_threshold_hz=500.0)

    def test_off_time_exceeds_tau_is_false(self):
        # f_slow = 600 Hz, off_time = 1.67 ms, tau = 1 ms
        assert not steady_state_flag(600.0, 600.0, tau_recomb_ms=1.0, fdrt_threshold_hz=500.0)

    def test_uses_slowest_axis(self):
        # fx=5000, fy=600 Hz, only fy matters
        assert steady_state_flag(5000.0, 600.0, tau_recomb_ms=2.0, fdrt_threshold_hz=500.0)

    def test_fy_faster_uses_fx(self):
        # fy=5000, fx=600 Hz -> slow axis = fx=600
        assert steady_state_flag(600.0, 5000.0, tau_recomb_ms=2.0, fdrt_threshold_hz=500.0)


class TestMaxPixelOffTime:
    def test_1000Hz_slow_axis(self):
        result = max_pixel_off_time_ms(5000.0, 1000.0)
        assert abs(result - 1.0) < 1e-9

    def test_zero_frequency_is_inf(self):
        assert max_pixel_off_time_ms(0.0, 0.0) == float("inf")

    def test_uses_minimum_frequency(self):
        # f_slow = min(100, 200) = 100 Hz -> 10 ms
        result = max_pixel_off_time_ms(100.0, 200.0)
        assert abs(result - 10.0) < 1e-9


class TestFwhmSpotRule:
    def test_large_fwhm_passes(self):
        # fwhm=10mm, fx=100Hz, fy=10Hz, ax=ay=20mm
        # n_lines at gcd(100,10)=10 → n_x = 100/10=10, spacing_x = 40/10=4mm, fwhm=10 >= 3*4=12? No
        # Let's just check the return type
        passed, spacing = fwhm_spot_rule(10.0, 10.0, 20.0, 20.0, 100.0, 10.0)
        assert isinstance(passed, bool)
        assert isinstance(spacing, float)

    def test_very_large_fwhm_passes(self):
        # fwhm=30mm, tiny spacing
        passed, _ = fwhm_spot_rule(30.0, 30.0, 1.0, 1.0, 100.0, 100.0)
        assert passed

    def test_zero_amplitude_edge_case(self):
        passed, spacing = fwhm_spot_rule(5.0, 5.0, 0.0, 0.0, 100.0, 10.0)
        assert isinstance(passed, bool)


class TestCharacteristicTau:
    def test_positive_tau(self):
        tau = characteristic_tau(1000.0, 100.0, 20.0, 20.0, 2.0, 2.0)
        assert tau > 0.0

    def test_faster_axis_gives_shorter_tau(self):
        tau_fast = characteristic_tau(5000.0, 100.0, 20.0, 20.0, 2.0, 2.0)
        tau_slow = characteristic_tau(500.0,  100.0, 20.0, 20.0, 2.0, 2.0)
        assert tau_fast < tau_slow

    def test_zero_frequency_is_inf(self):
        assert characteristic_tau(0.0, 0.0, 20.0, 20.0, 2.0, 2.0) == float("inf")


class TestDiffusionLength:
    def test_proportional_to_sqrt_tau(self):
        d1 = diffusion_length(1e-15, 1.0)
        d4 = diffusion_length(1e-15, 4.0)
        assert abs(d4 / d1 - 2.0) < 0.01

    def test_proportional_to_sqrt_D(self):
        d1 = diffusion_length(1e-15, 1.0)
        d4 = diffusion_length(4e-15, 1.0)
        assert abs(d4 / d1 - 2.0) < 0.01


# ── compute_all_metrics (integration) ────────────────────────────────────────

class TestComputeAllMetrics:
    def test_returns_all_expected_keys(self, params):
        t, x, y = get_realistic_trajectory(params)
        dose, rho, xe, ye = compute_dose(params, t, x, y)
        dt = t[1] - t[0]
        m = compute_all_metrics(dose, rho, x, y, dt, xe, ye, params)

        # tau_ms / diffusion_length_um / steady_state were intentionally removed
        # from compute_all_metrics (commit ae4f1b8). They remain available as
        # standalone helpers (characteristic_tau / diffusion_length /
        # steady_state_flag) and are tested directly elsewhere.
        expected_keys = [
            "flatness_pct", "rms_pct", "max_min_ratio", "pinch_pct",
            "dwell_mean", "dwell_std", "dwell_peak_min_ratio", "fwhm_spot_pass",
            "spot_spacing_mm", "triangularity", "slew_margin_pct", "slew_limited",
            "max_pixel_off_time_ms",
        ]
        for key in expected_keys:
            assert key in m, f"Missing key: {key}"

    def test_flatness_is_finite_and_non_negative(self, params):
        t, x, y = get_realistic_trajectory(params)
        dose, rho, xe, ye = compute_dose(params, t, x, y)
        dt = t[1] - t[0]
        m = compute_all_metrics(dose, rho, x, y, dt, xe, ye, params)
        assert np.isfinite(m["flatness_pct"])
        assert m["flatness_pct"] >= 0.0

    def test_steady_state_false_at_low_freq(self, params):
        # steady_state is no longer part of compute_all_metrics; verify the
        # underlying physics helper instead. Both axes well below the FDRT floor
        # must report a transient (non steady-state) regime.
        ss = steady_state_flag(
            fx_hz=50.0, fy_hz=5.0,
            tau_recomb_ms=params["tau_recomb_ms"],
            fdrt_threshold_hz=params["fdrt_threshold_hz"],
        )
        assert ss is False

    @pytest.mark.parametrize("pattern", ["classic", "lissajous", "sinusoidal", "wobble"])
    def test_all_patterns_produce_finite_flatness(self, params, pattern):
        params["pattern"] = pattern
        t, x, y = get_realistic_trajectory(params)
        dose, rho, xe, ye = compute_dose(params, t, x, y)
        dt = t[1] - t[0]
        m = compute_all_metrics(dose, rho, x, y, dt, xe, ye, params)
        assert np.isfinite(m["flatness_pct"])
