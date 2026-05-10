import numpy as np
from scipy.optimize import differential_evolution

from defaults import DEFAULTS
from patterns import get_pattern
from dose import compute_dose
from metrics import compute_all_metrics


def objective(params_vec, fixed_params):
    """
    Objective function for optimization.
    params_vec = [fx_hz, fy_hz, ax_overscan_factor, ay_overscan_factor]
    Returns scalar cost J (lower is better).
    """
    fx, fy, ax_factor, ay_factor = params_vec

    params = dict(fixed_params)
    params["fx_hz"] = fx
    params["fy_hz"] = fy
    # ax/ay derived from aperture half-widths × overscan factor
    half_x = (params["aperture_xR_mm"] - params["aperture_xL_mm"]) / 2.0
    half_y = (params["aperture_yT_mm"] - params["aperture_yB_mm"]) / 2.0
    params["ax_mm"] = half_x * ax_factor
    params["ay_mm"] = half_y * ay_factor

    try:
        t_arr, x_arr, y_arr = get_pattern(params["pattern"], params)
        dose, rho, x_edges, y_edges = compute_dose(params, t_arr, x_arr, y_arr)
        m = compute_all_metrics(dose, rho, x_arr, y_arr, t_arr[1] - t_arr[0], x_edges, y_edges, params)
    except Exception:
        return 1e9

    w1 = params.get("w1", DEFAULTS["w1"])
    w2 = params.get("w2", DEFAULTS["w2"])
    w3 = params.get("w3", DEFAULTS["w3"])
    w4 = params.get("w4", DEFAULTS["w4"])
    w5 = params.get("w5", DEFAULTS["w5"])
    bw = params.get("amplifier_bw_hz", DEFAULTS["amplifier_bw_hz"])
    fdrt_thresh = params.get("fdrt_threshold_hz", DEFAULTS["fdrt_threshold_hz"])

    J = (
        w1 * m["flatness_pct"]
        + w2 * abs(m["pinch_pct"])
        + w3 * max(0.0, fdrt_thresh - fx)
        + w4 * max(0.0, fx - bw)
        + w5 * max(0.0, 1.0 - m["dwell_mean"] / (m["dwell_mean"] + 1e-12))
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
    fx_vals = np.logspace(np.log10(500), np.log10(10000), n_fx)
    fy_vals = np.logspace(np.log10(1), np.log10(500), n_fy)

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
