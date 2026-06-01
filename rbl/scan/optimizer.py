import numpy as np
from scipy.optimize import differential_evolution

from defaults import DEFAULTS
from patterns import get_realistic_trajectory
from dose import compute_dose
from metrics import compute_all_metrics


def objective(params_vec, fixed_params):
    """
    Amplifier-aware optimizer objective with proper FDRT physics.

    params_vec = [fx_hz, fy_hz, ax_overscan_factor, ay_overscan_factor]
    Returns scalar cost J (lower = better).

    Trajectory is generated via get_realistic_trajectory(), so flatness/pinch
    metrics already account for amplifier filtering.

    Weights:
        w1 : flatness penalty (ASTM E521)
        w2 : pinch (edge-cusp) penalty
        w3 : sub-FDRT-threshold penalty on SLOW axis (min(fx, fy))    [was fx only]
        w4 : slew-rate violation penalty
        w5 : coverage penalty -- pixels that get little/no dose       [was no-op]
        w6 : triangularity loss penalty
        w7 : pixel off-time exceeds tau_recomb penalty                [NEW]

    See physics_refs.FDRT_REFS and physics_refs.PIXEL_REVISIT_RULE for
    sources and derivation.
    """
    fx, fy, ax_factor, ay_factor = params_vec

    params = dict(fixed_params)
    params["fx_hz"] = fx
    params["fy_hz"] = fy
    half_x = (params["aperture_xR_mm"] - params["aperture_xL_mm"]) / 2.0
    half_y = (params["aperture_yT_mm"] - params["aperture_yB_mm"]) / 2.0
    params["ax_mm"] = half_x * ax_factor
    params["ay_mm"] = half_y * ay_factor

    # --- Auto-scale T_total so each trial sees enough slow-axis cycles ---
    # Otherwise a slow fy makes the pattern under-developed and flatness lies.
    min_cycles = params.get("optimizer_min_slow_cycles", 5.0)
    T_max_ms   = params.get("optimizer_T_max_ms",        500.0)
    f_slow     = max(min(fx, fy), 1e-3)
    T_needed_ms = (min_cycles * 1000.0) / f_slow
    T_for_trial = min(max(T_needed_ms, params["T_total_ms"]), T_max_ms)
    params["T_total_ms"] = T_for_trial
    # Scale sample count proportionally so dt stays the same regime
    base_n   = params.get("n_time_samples", 50000)
    base_T   = max(fixed_params.get("T_total_ms", 100.0), 1.0)
    params["n_time_samples"] = int(base_n * T_for_trial / base_T)

    try:
        t_arr, x_arr, y_arr = get_realistic_trajectory(params)
        dose, rho, x_edges, y_edges = compute_dose(params, t_arr, x_arr, y_arr)
        m = compute_all_metrics(dose, rho, x_arr, y_arr,
                                t_arr[1] - t_arr[0], x_edges, y_edges, params)
    except Exception:
        return 1e9

    # --- Pull weights (read with .get so old configs still work) ---
    w1 = params.get("w1", DEFAULTS["w1"])
    w2 = params.get("w2", DEFAULTS["w2"])
    w3 = params.get("w3", DEFAULTS["w3"])
    w4 = params.get("w4", DEFAULTS["w4"])
    w5 = params.get("w5", DEFAULTS["w5"])
    w6 = params.get("w6", DEFAULTS.get("w6", 0.5))
    w7 = params.get("w7", DEFAULTS.get("w7", 2.0))

    fdrt_thresh   = params.get("fdrt_threshold_hz", DEFAULTS["fdrt_threshold_hz"])
    tau_recomb_ms = params.get("tau_recomb_ms",     DEFAULTS["tau_recomb_ms"])

    # --- Penalty: SLOW axis below FDRT floor (bug #1 fix) ---
    f_slow_axis = min(fx, fy)
    fdrt_penalty = max(0.0, fdrt_thresh - f_slow_axis)   # Hz

    # --- Penalty: pixel off-time exceeds tau_recomb (NEW w7) ---
    off_time_ms = m["max_pixel_off_time_ms"]
    revisit_penalty = max(0.0, off_time_ms - tau_recomb_ms) / max(tau_recomb_ms, 1e-9)
    # cap at 100x to avoid numerical blowup from tiny fy
    revisit_penalty = min(revisit_penalty, 100.0)

    # --- Slew penalty (unchanged) ---
    slew_violation = max(0.0, -m["slew_margin_pct"]) / 100.0

    # --- Triangularity loss (unchanged) ---
    triangularity_loss = max(0.0, 1.0 - m["triangularity"])

    # --- Coverage penalty (bug #3 fix) ---
    # If any pixel has zero dwell time, peak/min ratio is inf -> heavily penalized.
    pmr = m["dwell_peak_min_ratio"]
    if not np.isfinite(pmr):
        coverage_penalty = 100.0     # there's a hole in the coverage
    else:
        coverage_penalty = min(max(0.0, (pmr - 2.0) / 8.0), 10.0)
        # 0 when pmr<=2 (very flat), saturates at 10 when pmr>=82

    J = (
        w1 * m["flatness_pct"]
      + w2 * abs(m["pinch_pct"])
      + w3 * fdrt_penalty * 0.01            # scale Hz -> ~unit
      + w4 * slew_violation * 100.0
      + w5 * coverage_penalty * 10.0        # comparable to flatness%
      + w6 * triangularity_loss * 100.0
      + w7 * revisit_penalty * 10.0
    )
    return float(J)


def run_optimizer(bounds, fixed_params, weights=None):
    """
    Run differential-evolution optimizer.
    bounds: list of (min, max) for [fx, fy, ax_factor, ay_factor].
    Returns scipy OptimizeResult.
    """
    if weights:
        fixed_params = dict(fixed_params)
        for k, v in weights.items():
            fixed_params[k] = v

    result = differential_evolution(
        objective,
        bounds,
        args=(fixed_params,),
        workers=-1,
        updating="deferred",
        polish=True,
        maxiter=100,
        popsize=15,
        seed=42,
        tol=1e-4,
    )
    return result


def grid_search(fixed_params, n_fx=10, n_fy=10):
    """
    Evaluate objective on a log-spaced (fx, fy) grid.
    Returns (fx_vals, fy_vals, J_grid) where J_grid.shape == (n_fx, n_fy).
    """
    fy_min = fixed_params.get("optimizer_fy_min_hz", DEFAULTS["optimizer_fy_min_hz"])
    fdrt   = fixed_params.get("fdrt_threshold_hz",   DEFAULTS["fdrt_threshold_hz"])
    fx_vals = np.logspace(np.log10(max(fdrt, fy_min)), np.log10(15000), n_fx)
    fy_vals = np.logspace(np.log10(fy_min),            np.log10(5000),  n_fy)

    # Keep overscan factors at default 1.3
    half_x = (fixed_params["aperture_xR_mm"] - fixed_params["aperture_xL_mm"]) / 2.0
    half_y = (fixed_params["aperture_yT_mm"] - fixed_params["aperture_yB_mm"]) / 2.0
    ax_factor = fixed_params.get("ax_mm", DEFAULTS["ax_mm"]) / half_x if half_x != 0 else 1.3
    ay_factor = fixed_params.get("ay_mm", DEFAULTS["ay_mm"]) / half_y if half_y != 0 else 1.3
    ax_factor = np.clip(ax_factor, 1.0, 1.5)
    ay_factor = np.clip(ay_factor, 1.0, 1.5)

    J_grid = np.zeros((n_fx, n_fy))
    for i, fx in enumerate(fx_vals):
        for j, fy in enumerate(fy_vals):
            J_grid[i, j] = objective([fx, fy, ax_factor, ay_factor], fixed_params)

    return fx_vals, fy_vals, J_grid
