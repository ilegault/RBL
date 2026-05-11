import numpy as np
from math import gcd

from dose import trajectory_density


def flatness_pct(dose_inside: np.ndarray) -> float:
    """ASTM E521 flatness: (max-min)/(max+min)*100. Target <= 10%."""
    mx, mn = dose_inside.max(), dose_inside.min()
    if mx + mn == 0:
        return 0.0
    return (mx - mn) / (mx + mn) * 100.0


def rms_deviation_pct(dose_inside: np.ndarray) -> float:
    """RMS deviation relative to mean, in percent."""
    mu = dose_inside.mean()
    if mu == 0:
        return 0.0
    return dose_inside.std() / mu * 100.0


def max_min_ratio(dose_inside: np.ndarray) -> float:
    """Peak-to-valley ratio."""
    mn = dose_inside.min()
    if mn == 0:
        return float("inf")
    return dose_inside.max() / mn


def pinch_metric(dose, x_edges, y_edges, aperture) -> float:
    """
    Pinch (edge cusps): extra dose at X-turnaround edges.
    Takes horizontal slice through aperture centre row.
    aperture = (xL, xR, yB, yT).
    """
    xL, xR, yB, yT = aperture
    x_centers = 0.5 * (x_edges[:-1] + x_edges[1:])
    y_centers = 0.5 * (y_edges[:-1] + y_edges[1:])

    y_in = np.where((y_centers > yB) & (y_centers < yT))[0]
    x_in = np.where((x_centers > xL) & (x_centers < xR))[0]

    if len(y_in) == 0 or len(x_in) == 0:
        return 0.0

    center_row = int(y_in[len(y_in) // 2])
    row_slice  = dose[x_in, center_row]

    if row_slice.sum() == 0:
        return 0.0

    n        = len(row_slice)
    n_edge   = max(1, int(0.10 * n))
    n_center = max(1, int(0.20 * n))

    d_edge   = 0.5 * (row_slice[:n_edge].mean() + row_slice[-n_edge:].mean())
    half_c   = n_center // 2
    c_start  = n // 2 - half_c
    c_end    = n // 2 + half_c
    d_center = row_slice[c_start:c_end].mean()
    d_mean   = row_slice.mean()

    if d_mean == 0:
        return 0.0
    return (d_edge - d_center) / d_mean * 100.0


def dwell_stats(rho: np.ndarray, aperture_mask: np.ndarray) -> dict:
    """Statistics of dwell-time density inside aperture."""
    vals = rho[aperture_mask > 0]
    if len(vals) == 0:
        return {"mean": 0.0, "std": 0.0, "peak_min_ratio": float("inf")}
    mn = vals.min()
    return {
        "mean": float(vals.mean()),
        "std":  float(vals.std()),
        "peak_min_ratio": float(vals.max() / mn) if mn > 0 else float("inf"),
    }


def duty_cycle_per_pixel(x_traj, y_traj, dt, x_edges, y_edges, aperture_mask):
    """Fraction of total time beam centroid was within each pixel."""
    rho     = trajectory_density(x_traj, y_traj, dt, x_edges, y_edges)
    T_total = rho.sum()
    if T_total == 0:
        return np.zeros_like(rho)
    dc  = rho / T_total
    dc *= aperture_mask.astype(float)
    return dc


def characteristic_tau(fx_hz: float, fy_hz: float,
                       x_amp_mm: float, y_amp_mm: float,
                       fwhm_x_mm: float, fwhm_y_mm: float) -> float:
    """
    Characteristic pulse duration in ms: fwhm / (4 * amp * f) for the
    FAST axis (whichever of fx, fy is higher).
    Axis-symmetric: does not assume fx is always the fast axis.
    """
    if fx_hz >= fy_hz:
        v_beam = 4.0 * x_amp_mm * fx_hz
        fwhm   = fwhm_x_mm
    else:
        v_beam = 4.0 * y_amp_mm * fy_hz
        fwhm   = fwhm_y_mm
    if v_beam == 0:
        return float("inf")
    return fwhm / v_beam * 1000.0  # ms


def diffusion_length(D_i_m2s: float, tau_ms: float) -> float:
    """Interstitial diffusion length in micrometers."""
    return np.sqrt(D_i_m2s * tau_ms * 1e-3) * 1e6  # μm


def steady_state_flag(
    fx_hz: float,
    fy_hz: float,
    tau_recomb_ms: float,
    fdrt_threshold_hz: float = 500.0,
) -> bool:
    """
    True if beam operates in FDRT steady-state regime.
    Axis-symmetric: uses the FASTER of fx/fy as the revisit frequency.
    Condition: fast frequency >= FDRT threshold AND revisit period <= tau_recomb.
    """
    f_fast = max(fx_hz, fy_hz)
    if f_fast < fdrt_threshold_hz:
        return False
    revisit_ms = 1000.0 / f_fast
    return revisit_ms <= tau_recomb_ms


def fwhm_spot_rule(fwhm_x_mm: float, fwhm_y_mm: float,
                   ax_mm: float, ay_mm: float,
                   fx_hz: float, fy_hz: float) -> tuple:
    """
    True (PASS) if fwhm >= 3 * spot_spacing for BOTH axes independently.
    Axis-symmetric: checks each axis and returns the worst-case result.
    Returns (pass_flag, worst_spot_spacing_mm).
    """
    results = []
    for amp, fwhm in [(ax_mm, fwhm_x_mm), (ay_mm, fwhm_y_mm)]:
        try:
            n_lines = int(fx_hz) // gcd(int(fx_hz), int(fy_hz))
        except ZeroDivisionError:
            results.append((True, 0.0))
            continue
        if n_lines == 0:
            results.append((True, 0.0))
            continue
        spacing = 2.0 * amp / n_lines
        results.append((fwhm >= 3.0 * spacing, spacing))

    worst_pass    = all(r[0] for r in results)
    worst_spacing = max(r[1] for r in results)
    return worst_pass, worst_spacing


def _aperture_mask_from_edges(x_edges, y_edges, xL, xR, yB, yT):
    x_centers = 0.5 * (x_edges[:-1] + x_edges[1:])
    y_centers = 0.5 * (y_edges[:-1] + y_edges[1:])
    X, Y = np.meshgrid(x_centers, y_centers, indexing="ij")
    return (X > xL) & (X < xR) & (Y > yB) & (Y < yT)


def compute_all_metrics(dose, rho, x_traj, y_traj, dt, x_edges, y_edges, params) -> dict:
    """Master metrics function. Returns flat dict of all computed metrics."""
    xL = params["aperture_xL_mm"]
    xR = params["aperture_xR_mm"]
    yB = params["aperture_yB_mm"]
    yT = params["aperture_yT_mm"]

    mask       = _aperture_mask_from_edges(x_edges, y_edges, xL, xR, yB, yT)
    dose_inside = dose[mask]

    if dose_inside.size == 0 or dose_inside.sum() == 0:
        dose_inside = np.array([1.0])

    flat  = flatness_pct(dose_inside)
    rms   = rms_deviation_pct(dose_inside)
    mmr   = max_min_ratio(dose_inside)
    pinch = pinch_metric(dose, x_edges, y_edges, (xL, xR, yB, yT))
    dw    = dwell_stats(rho, mask)

    tau = characteristic_tau(
        params["fx_hz"],      params["fy_hz"],
        params["ax_mm"],      params["ay_mm"],
        params["fwhm_x_mm"], params["fwhm_y_mm"],
    )
    diff_len = diffusion_length(params["D_interstitial_m2s"], tau)

    ss = steady_state_flag(
        params["fx_hz"],
        params["fy_hz"],
        params["tau_recomb_ms"],
        params["fdrt_threshold_hz"],
    )
    fwhm_pass, spot_spacing = fwhm_spot_rule(
        params["fwhm_x_mm"], params["fwhm_y_mm"],
        params["ax_mm"],     params["ay_mm"],
        params["fx_hz"],     params["fy_hz"],
    )

    return {
        "flatness_pct":        flat,
        "rms_pct":             rms,
        "max_min_ratio":       mmr,
        "pinch_pct":           pinch,
        "dwell_mean":          dw["mean"],
        "dwell_std":           dw["std"],
        "dwell_peak_min_ratio": dw["peak_min_ratio"],
        "tau_ms":              tau,
        "diffusion_length_um": diff_len,
        "steady_state":        ss,
        "fwhm_spot_pass":      fwhm_pass,
        "spot_spacing_mm":     spot_spacing,
    }
