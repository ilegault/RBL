"""
8 validation tests for the raster scan tool.
Run: python validation.py
"""
import sys
import io
# Force UTF-8 output on Windows to avoid cp1252 encode errors
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
else:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import numpy as np

from rbla.config.defaults import DEFAULTS
from rbla.scan.patterns import get_realistic_trajectory, get_pattern
from rbla.scan.dose import compute_dose
from rbla.scan.metrics import (
    flatness_pct, steady_state_flag, pinch_metric,
    _aperture_mask_from_edges, )


def _run(params, pattern=None):
    p = dict(DEFAULTS)
    p.update(params)
    if pattern is not None:
        p["pattern"] = pattern
    p["simulate_amplifier"] = False  # existing tests assume ideal trajectory
    t, x, y = get_realistic_trajectory(p)
    dose, rho, xe, ye = compute_dose(p, t, x, y)
    return dose, rho, xe, ye, t, x, y, p


results = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" -- {detail}" if detail else ""))
    results.append(condition)
    return condition


# --- Test 1: Static Gaussian (no scan) ----------------------------------------
def test1():
    print("\nTest 1: Static Gaussian")
    p = dict(DEFAULTS)
    p.update({
        "ax_mm": 0.0, "ay_mm": 0.0,
        "aperture_xL_mm": -20.0, "aperture_xR_mm": 20.0,
        "aperture_yB_mm": -20.0, "aperture_yT_mm": 20.0,
        "grid_nx": 128, "grid_ny": 128,
        "T_total_ms": 10.0, "n_time_samples": 1000,
        "pattern": "classic",
    })
    t, x, y = get_pattern("classic", p)
    x[:] = 0.0
    y[:] = 0.0
    dose, rho, xe, ye = compute_dose(p, t, x, y)

    # Peak should be near physical origin
    ix = np.argmin(np.abs(0.5 * (xe[:-1] + xe[1:])))
    iy = np.argmin(np.abs(0.5 * (ye[:-1] + ye[1:])))
    peak_loc = np.unravel_index(np.argmax(dose), dose.shape)

    ok = abs(peak_loc[0] - ix) <= 2 and abs(peak_loc[1] - iy) <= 2
    check("Peak at origin", ok, f"peak pixel={peak_loc}, expected approx ({ix},{iy})")

    # Radially decreasing Gaussian — check only above noise floor
    cx, cy = 0.5 * (xe[:-1] + xe[1:]), 0.5 * (ye[:-1] + ye[1:])
    X, Y = np.meshgrid(cx, cy, indexing="ij")
    R = np.sqrt(X**2 + Y**2)
    r_bins = np.linspace(0, R.max(), 20)
    peak_val = dose[peak_loc]
    noise_floor = peak_val * 1e-6  # ignore FFT floating-point noise beyond 6 sigma
    last_mean = peak_val
    monotone = True
    for r0, r1 in zip(r_bins[:-1], r_bins[1:]):
        mask = (R >= r0) & (R < r1)
        if mask.sum() > 0:
            m = dose[mask].mean()
            if last_mean > noise_floor and m > last_mean * 1.05:
                monotone = False
                break
            if m > noise_floor:
                last_mean = m
    check("Radially decreasing Gaussian", monotone)


# --- Test 2: Lissajous coverage ------------------------------------------------
def test2():
    print("\nTest 2: Lissajous coverage -- quasi-uniform fill (5450/6700 Hz, 500 ms)")
    p = dict(DEFAULTS)
    p.update({
        "fx_hz": 5450.0, "fy_hz": 6700.0,
        "ax_mm": 8.0, "ay_mm": 10.0,
        "aperture_xL_mm": -7.0, "aperture_xR_mm": 7.0,
        "aperture_yB_mm": -9.0, "aperture_yT_mm": 9.0,
        "fwhm_x_mm": 1.0, "fwhm_y_mm": 1.0,
        "T_total_ms": 500.0, "n_time_samples": 200000,
        "grid_nx": 128, "grid_ny": 128,
        "pattern": "lissajous",
    })
    dose, rho, xe, ye, t, x, y, p = _run(p)
    mask = _aperture_mask_from_edges(xe, ye,
        p["aperture_xL_mm"], p["aperture_xR_mm"],
        p["aperture_yB_mm"], p["aperture_yT_mm"])
    dose_inside = dose[mask]
    # Lissajous has arcsin^2 density (higher at corners) -- full uniformity is not
    # achievable with small FWHM. Check: all pixels have non-zero dose, flatness < 80%.
    all_nonzero = dose_inside.min() > 0
    check("All aperture pixels have non-zero dose", all_nonzero,
          f"min dose={dose_inside.min():.4e}")
    flat = flatness_pct(dose_inside)
    check("Lissajous flatness < 80% (quasi-uniform fill)", flat < 80.0,
          f"flatness={flat:.2f}%")


# --- Test 3: Spot equals aperture -----------------------------------------------
def test3():
    print("\nTest 3: Spot = aperture (FWHM = aperture width, no scan)")
    p = dict(DEFAULTS)
    fwhm = 10.0
    p.update({
        "fwhm_x_mm": fwhm, "fwhm_y_mm": fwhm,
        "ax_mm": 0.0, "ay_mm": 0.0,
        "aperture_xL_mm": -5.0, "aperture_xR_mm": 5.0,
        "aperture_yB_mm": -5.0, "aperture_yT_mm": 5.0,
        "grid_nx": 128, "grid_ny": 128,
        "T_total_ms": 10.0, "n_time_samples": 500,
        "pattern": "classic",
    })
    t, x, y = get_pattern("classic", p)
    x[:] = 0.0
    y[:] = 0.0
    dose, rho, xe, ye = compute_dose(p, t, x, y)

    mask = _aperture_mask_from_edges(xe, ye, -5, 5, -5, 5)
    total_inside = dose[mask].sum()
    total_all = dose.sum()
    frac = total_inside / total_all if total_all > 0 else 0
    # Wide Gaussian (FWHM=10mm) inside +-5mm aperture: most dose inside
    check("Significant dose inside aperture (>50%)", frac > 0.5,
          f"fraction inside={frac:.3f}")


# --- Test 4: Pinch reproduction -------------------------------------------------
def test4():
    print("\nTest 4: Pinch metric -- sinusoidal raster (cusping at turnarounds)")
    p = dict(DEFAULTS)
    p.update({
        "fx_hz": 500.0, "fy_hz": 5.0,
        # No x-overscan: turnarounds at aperture edge => strong cusping
        "ax_mm": 10.0, "ay_mm": 13.0,
        "aperture_xL_mm": -10.0, "aperture_xR_mm": 10.0,
        "aperture_yB_mm": -10.0, "aperture_yT_mm": 10.0,
        "fwhm_x_mm": 0.5, "fwhm_y_mm": 0.5,
        "T_total_ms": 400.0, "n_time_samples": 100000,
        "grid_nx": 128, "grid_ny": 128,
        "pattern": "sinusoidal",
    })
    dose, rho, xe, ye, t, x, y, p = _run(p)
    pinch = pinch_metric(dose, xe, ye,
        (p["aperture_xL_mm"], p["aperture_xR_mm"],
         p["aperture_yB_mm"], p["aperture_yT_mm"]))
    # Sinusoidal pattern slows at x = +-ax = aperture edge => edge dose > center
    check("Pinch > 10% (sinusoidal cusp at aperture edge)", pinch > 10.0,
          f"pinch={pinch:.2f}%")


# --- Test 5: Lissajous fill factor (Hwang 2017) ---------------------------------
def test5():
    print("\nTest 5: Lissajous fill factor (5450/6700 Hz, Hwang 2017)")
    p = dict(DEFAULTS)
    p.update({
        "fx_hz": 5450.0, "fy_hz": 6700.0,
        "ax_mm": 8.0, "ay_mm": 8.0,
        "aperture_xL_mm": -7.0, "aperture_xR_mm": 7.0,
        "aperture_yB_mm": -7.0, "aperture_yT_mm": 7.0,
        "fwhm_x_mm": 1.5, "fwhm_y_mm": 1.5,
        "T_total_ms": 300.0, "n_time_samples": 150000,
        "grid_nx": 128, "grid_ny": 128,
        "pattern": "lissajous",
    })
    dose, rho, xe, ye, t, x, y, p = _run(p)
    mask = _aperture_mask_from_edges(xe, ye,
        p["aperture_xL_mm"], p["aperture_xR_mm"],
        p["aperture_yB_mm"], p["aperture_yT_mm"])
    dose_inside = dose[mask]
    all_nonzero = dose_inside.min() > 0
    flat = flatness_pct(dose_inside)
    check("Lissajous 5450/6700 Hz: all pixels non-zero", all_nonzero,
          f"min={dose_inside.min():.4e}")
    check("Lissajous 5450/6700 Hz: flatness < 80%", flat < 80.0,
          f"flatness={flat:.2f}%")


# --- Test 6: MIBL-spec reproduction ---------------------------------------------
def test6():
    print("\nTest 6: MIBL canonical parameters (2061/255 Hz)")
    dose, rho, xe, ye, t, x, y, p = _run({})  # use all defaults
    mask = _aperture_mask_from_edges(xe, ye,
        p["aperture_xL_mm"], p["aperture_xR_mm"],
        p["aperture_yB_mm"], p["aperture_yT_mm"])
    flat = flatness_pct(dose[mask])
    check("MIBL flatness <= 10% (ASTM E521)", flat <= 10.0, f"flatness={flat:.2f}%")
    check("MIBL flatness not obviously broken (< 50%)", flat < 50.0,
          f"flatness={flat:.2f}%")


# --- Test 7: FDRT steady-state toggle -------------------------------------------
def test7():
    print("\nTest 7: FDRT steady-state flag uses slow axis (min(fx, fy))")
    # Both axes below FDRT floor -> TRANSIENT
    ss_both_slow = steady_state_flag(100.0, 255.0, 1.0, 500.0)
    # Fast axis above floor but slow axis (fy=255) still below floor -> TRANSIENT
    ss_fast_x_only = steady_state_flag(2061.0, 255.0, 1.0, 500.0)
    # Both axes >= 500 Hz and revisit_ms = 1000/1000 = 1 ms = tau -> STEADY
    ss_both_fast = steady_state_flag(2000.0, 1000.0, 1.0, 500.0)
    check("fx=100, fy=255 Hz -> TRANSIENT (False)", ss_both_slow is False, f"got {ss_both_slow}")
    check("fx=2061, fy=255 Hz -> TRANSIENT (fy below FDRT floor)", ss_fast_x_only is False,
          f"got {ss_fast_x_only}")
    check("fx=2000, fy=1000 Hz -> STEADY (True)", ss_both_fast is True, f"got {ss_both_fast}")


# --- Test 8: Dose conservation --------------------------------------------------
def test8():
    print("\nTest 8: Dose conservation (rho integral = T_total)")
    p = dict(DEFAULTS)
    p.update({
        "aperture_xL_mm": -30.0, "aperture_xR_mm": 30.0,
        "aperture_yB_mm": -30.0, "aperture_yT_mm": 30.0,
        "ax_mm": 6.5, "ay_mm": 9.1,
        "grid_nx": 256, "grid_ny": 256,
    })
    dose, rho, xe, ye, t, x, y, p = _run(p)

    T_total = p["T_total_ms"] * 1e-3

    # rho is already dt-weighted by trajectory_density; rho.sum() == T_total
    rho_integral = rho.sum()
    rel_err_rho = abs(rho_integral - T_total) / T_total
    check("Dwell-time integral = T_total (< 2% error)", rel_err_rho < 0.02,
          f"rho.sum()={rho_integral:.6f} s, T_total={T_total:.6f} s, "
          f"rel_err={rel_err_rho*100:.4f}%")

    dx = (xe[-1] - xe[0]) / p["grid_nx"]
    dy = (ye[-1] - ye[0]) / p["grid_ny"]
    dose_integral = dose.sum() * dx * dy
    check("Dose sum is finite and positive",
          np.isfinite(dose_integral) and dose_integral > 0,
          f"dose_integral={dose_integral:.4g}")


# --- Test 9: Amplifier -- low frequency passes through unchanged --------------
def test9():
    print("\nTest 9: Amplifier off vs on at LOW fx -- should be nearly identical")
    p = dict(DEFAULTS)
    p.update({
        "fx_hz": 500.0,
        "fy_hz": 50.0,
        "T_total_ms": 100.0,
        "n_time_samples": 50000,
        "simulate_amplifier": False,
    })
    t, x_ideal, y = get_realistic_trajectory(p)
    p["simulate_amplifier"] = True
    _, x_real, _ = get_realistic_trajectory(p)
    rel_err = np.max(np.abs(x_ideal - x_real)) / (np.max(np.abs(x_ideal)) + 1e-12)
    check("At 500 Hz, filtered trajectory matches ideal within 5%", rel_err < 0.05,
          f"rel_err={rel_err:.4f}")


# --- Test 10: Amplifier -- high fx causes measurable rounding -----------------
def test10():
    print("\nTest 10: At fx = 15 kHz (above BW), waveform must be rounded")
    from rbla.scan.metrics import triangularity_score
    p = dict(DEFAULTS)
    p.update({
        "fx_hz": 15000.0,
        "fy_hz": 50.0,
        "T_total_ms": 30.0,
        "n_time_samples": 100000,
        "simulate_amplifier": False,
    })
    t, x_ideal, _ = get_realistic_trajectory(p)
    p["simulate_amplifier"] = True
    _, x_real, _ = get_realistic_trajectory(p)
    tri_ideal = triangularity_score(x_ideal, t, 15000.0)
    tri_real  = triangularity_score(x_real,  t, 15000.0)
    check("Triangularity drops at 15 kHz (BW=10 kHz)", tri_real < tri_ideal - 0.02,
          f"ideal={tri_ideal:.3f}, real={tri_real:.3f}")


# --- Test 11: Slew clamp respects the spec ------------------------------------
def test11():
    print("\nTest 11: Slew clamp enforces 300 V/us")
    from rbla.physics.amplifier import apply_slew_limit
    n = 10000
    dt = 1.0e-7   # 100 ns sampling
    # 5 kV step
    sig = np.concatenate([np.zeros(500), np.full(n - 500, 5000.0)])
    out = apply_slew_limit(sig, dt, slew_max_V_per_s=300.0e6)
    peak_slope = np.max(np.abs(np.diff(out)) / dt)
    check("Peak |dV/dt| <= 300 V/us + 0.1% tolerance", peak_slope <= 300.0e6 * 1.001,
          f"peak slope = {peak_slope:.3e} V/s")


# --- Test 12: Regression -- toggle off reproduces pre-amplifier behavior ------
def test12():
    print("\nTest 12: simulate_amplifier=False is identical to legacy ideal trajectory")
    p = dict(DEFAULTS)
    p["simulate_amplifier"] = False
    t_legacy, x_legacy, y_legacy = get_pattern(p["pattern"], p)
    t_new,    x_new,    y_new    = get_realistic_trajectory(p)
    same = (np.allclose(t_legacy, t_new) and
            np.allclose(x_legacy, x_new) and
            np.allclose(y_legacy, y_new))
    check("Toggle off reproduces get_pattern() exactly", same,
          f"max x diff = {np.max(np.abs(x_legacy - x_new)):.2e}")


# --- Test 13: FDRT penalty fires on slow fy --------------------------------
def test13():
    print("\nTest 13: Objective punishes fy=14 (the bug from the screenshot)")
    from rbla.scan.optimizer import objective
    p = dict(DEFAULTS)
    p["simulate_amplifier"] = True
    # Reproduce the bad result from the user's screenshot
    J_bad = objective([579.0, 14.0, 1.471, 1.448], p)
    # And a sensible operating point
    J_good = objective([2000.0, 600.0, 1.3, 1.3], p)
    check("J(fx=579, fy=14) > J(fx=2000, fy=600)", J_bad > J_good,
          f"J_bad={J_bad:.3f}, J_good={J_good:.3f}")


# --- Test 14: steady_state_flag uses the slow axis -------------------------
def test14():
    print("\nTest 14: steady_state_flag uses min(fx, fy), not max")
    from rbla.scan.metrics import steady_state_flag
    # max(fx, fy) = 5000 Hz (would pass under the old buggy logic)
    # min(fx, fy) = 100 Hz (well below FDRT floor of 500)
    ss = steady_state_flag(fx_hz=5000.0, fy_hz=100.0,
                           tau_recomb_ms=1.0, fdrt_threshold_hz=500.0)
    check("steady_state_flag rejects slow axis below FDRT floor", ss is False,
          f"got steady_state={ss}")


# --- Test 15: max_pixel_off_time_ms reports the slow-axis period ----------
def test15():
    print("\nTest 15: max_pixel_off_time_ms reports 1/min(fx,fy)")
    from rbla.scan.metrics import max_pixel_off_time_ms
    off = max_pixel_off_time_ms(fx_hz=2000.0, fy_hz=50.0)
    expected = 1000.0 / 50.0
    check("max off-time matches 1/fy when fy<fx", abs(off - expected) < 1e-6,
          f"off={off:.3f}, expected={expected:.3f}")


# --- Main -----------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 60)
    print("Raster Scan Tool -- Validation Suite")
    print("=" * 60)

    test1()
    test2()
    test3()
    test4()
    test5()
    test6()
    test7()
    test8()
    test9()
    test10()
    test11()
    test12()
    test13()
    test14()
    test15()

    n_pass = sum(results)
    n_total = len(results)
    print(f"\n{'='*60}")
    print(f"SUMMARY: {n_pass}/{n_total} tests passed.")
    print("=" * 60)

    sys.exit(0 if n_pass == n_total else 1)
