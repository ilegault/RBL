"""
Streamlit GUI for the Raster Scan Analysis Tool.
Run: streamlit run gui.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import io
import numpy as np
import streamlit as st
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from defaults import DEFAULTS
from patterns import get_pattern
from dose import compute_dose
from metrics import compute_all_metrics, _aperture_mask_from_edges
from viz import plot_heatmap, animate_trajectory, plot_dwell_hist
from optimizer import run_optimizer, grid_search

st.set_page_config(page_title="Raster Scan Tool", layout="wide")
st.title("Ion-Beam Raster Scan Analysis Tool")
st.caption("UW-IBL / MIBL — beam uniformity optimizer")

# ─── Sidebar ─────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Beam")
    fwhm_x = st.slider("FWHM X (mm)", 0.5, 5.0, DEFAULTS["fwhm_x_mm"], 0.1)
    fwhm_y = st.slider("FWHM Y (mm)", 0.5, 5.0, DEFAULTS["fwhm_y_mm"], 0.1)

    st.header("Aperture")
    col1, col2 = st.columns(2)
    with col1:
        apert_xL = st.number_input("xL (mm)", -25.0, 0.0,   DEFAULTS["aperture_xL_mm"], 0.5)
        apert_yB = st.number_input("yB (mm)", -25.0, 0.0,   DEFAULTS["aperture_yB_mm"], 0.5)
    with col2:
        apert_xR = st.number_input("xR (mm)", 0.0,  25.0,  DEFAULTS["aperture_xR_mm"], 0.5)
        apert_yT = st.number_input("yT (mm)", 0.0,  25.0,  DEFAULTS["aperture_yT_mm"], 0.5)

    st.header("Scan Parameters")
    half_x = (apert_xR - apert_xL) / 2.0
    half_y = (apert_yT - apert_yB) / 2.0

    ax_factor = st.slider("X overscan factor", 1.0, 1.5,
                          float(np.clip(DEFAULTS["ax_mm"] / half_x if half_x > 0 else 1.3, 1.0, 1.5)), 0.01)
    ay_factor = st.slider("Y overscan factor", 1.0, 1.5,
                          float(np.clip(DEFAULTS["ay_mm"] / half_y if half_y > 0 else 1.3, 1.0, 1.5)), 0.01)
    ax_mm = half_x * ax_factor
    ay_mm = half_y * ay_factor

    fx_hz = st.slider("f_x (Hz) — fast axis", 500, 10000,
                      int(DEFAULTS["fx_hz"]), 10,
                      key="fx_slider")
    fy_hz = st.slider("f_y (Hz) — slow axis", 1, 500,
                      int(DEFAULTS["fy_hz"]), 1,
                      key="fy_slider")

    st.header("Pattern")
    pattern = st.selectbox("Scan pattern", ["classic", "alt_axes", "lissajous", "spiral", "sinusoidal", "wobble"],
                           index=0)
    phase_deg = 0.0
    if pattern == "lissajous":
        phase_deg = st.slider("Lissajous phase (°)", 0.0, 360.0, DEFAULTS["lissajous_phase_deg"], 1.0)

    st.header("Simulation")
    T_ms = st.slider("T_total (ms)", 10.0, 1000.0, DEFAULTS["T_total_ms"], 10.0)
    n_samples = st.slider("Time samples", 10000, 500000, DEFAULTS["n_time_samples"], 1000)
    grid_nx = st.slider("Grid Nx", 64, 512, DEFAULTS["grid_nx"], 64)
    grid_ny = st.slider("Grid Ny", 64, 512, DEFAULTS["grid_ny"], 64)

    st.header("Physics")
    tau_recomb = st.slider("τ_recomb (ms)", 0.1, 10.0, DEFAULTS["tau_recomb_ms"], 0.1)
    D_i = st.select_slider("D_i (m²/s)", options=[1e-11, 1e-10, 1e-9, 1e-8, 1e-7],
                            value=DEFAULTS["D_interstitial_m2s"],
                            format_func=lambda v: f"{v:.0e}")
    fdrt_thresh = st.slider("FDRT threshold (Hz)", 100, 1000, int(DEFAULTS["fdrt_threshold_hz"]), 10)

    st.header("Optimizer Weights")
    w1 = st.slider("w1 (flatness)", 0.0, 2.0, DEFAULTS["w1"], 0.1)
    w2 = st.slider("w2 (pinch)", 0.0, 2.0, DEFAULTS["w2"], 0.1)
    w3 = st.slider("w3 (FDRT penalty)", 0.0, 2.0, DEFAULTS["w3"], 0.1)
    w4 = st.slider("w4 (BW penalty)", 0.0, 2.0, DEFAULTS["w4"], 0.1)
    w5 = st.slider("w5 (dose conserv.)", 0.0, 2.0, DEFAULTS["w5"], 0.1)

# ─── Build params dict ────────────────────────────────────────────────────────
params = {
    "fwhm_x_mm": fwhm_x,
    "fwhm_y_mm": fwhm_y,
    "aperture_xL_mm": apert_xL,
    "aperture_xR_mm": apert_xR,
    "aperture_yB_mm": apert_yB,
    "aperture_yT_mm": apert_yT,
    "ax_mm": ax_mm,
    "ay_mm": ay_mm,
    "fx_hz": float(fx_hz),
    "fy_hz": float(fy_hz),
    "pattern": pattern,
    "lissajous_phase_deg": phase_deg,
    "T_total_ms": T_ms,
    "n_time_samples": n_samples,
    "grid_nx": grid_nx,
    "grid_ny": grid_ny,
    "tau_recomb_ms": tau_recomb,
    "D_interstitial_m2s": D_i,
    "fdrt_threshold_hz": float(fdrt_thresh),
    "amplifier_bw_hz": DEFAULTS["amplifier_bw_hz"],
    "flatness_target_pct": DEFAULTS["flatness_target_pct"],
    "w1": w1, "w2": w2, "w3": w3, "w4": w4, "w5": w5,
}

# ─── Cached pipeline ──────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False)
def run_pipeline(params_tuple):
    p = dict(params_tuple)
    t, x, y = get_pattern(p["pattern"], p)
    dose, rho, xe, ye = compute_dose(p, t, x, y)
    m = compute_all_metrics(dose, rho, x, y, t[1] - t[0], xe, ye, p)
    return dose, rho, xe, ye, t, x, y, m


params_tuple = tuple(sorted(params.items()))
with st.spinner("Computing dose map..."):
    dose, rho, xe, ye, t_arr, x_arr, y_arr, metrics = run_pipeline(params_tuple)

aperture_rect = (params["aperture_xL_mm"], params["aperture_xR_mm"],
                 params["aperture_yB_mm"], params["aperture_yT_mm"])

# ─── Tabs ─────────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4 = st.tabs(["Dose Map", "Trajectory", "Metrics", "Optimizer"])

# ── Tab 1: Dose Map ──────────────────────────────────────────────────────────
with tab1:
    fig = plot_heatmap(dose, xe, ye, aperture_rect, metrics)
    st.pyplot(fig)
    plt.close(fig)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Flatness %", f"{metrics['flatness_pct']:.2f}%",
              delta=f"{metrics['flatness_pct'] - 10:.2f}% vs 10% target",
              delta_color="inverse")
    c2.metric("Pinch %",   f"{metrics['pinch_pct']:.2f}%")
    c3.metric("RMS %",     f"{metrics['rms_pct']:.2f}%")
    c4.metric("Max/Min",   f"{metrics['max_min_ratio']:.3f}")

    st.divider()
    col_ss, col_fwhm = st.columns(2)
    with col_ss:
        if metrics["steady_state"]:
            st.success("STEADY STATE (FDRT regime)")
        else:
            st.error("TRANSIENT — f_x below FDRT threshold or pixel cycle > τ_recomb")
    with col_fwhm:
        if metrics["fwhm_spot_pass"]:
            st.success(f"FWHM OK — {fwhm_x:.1f} mm >= 3× spot spacing ({metrics['spot_spacing_mm']:.3f} mm)")
        else:
            st.warning(f"FWHM < 3× spot spacing ({metrics['spot_spacing_mm']:.3f} mm) — striping likely")

# ── Tab 2: Trajectory ────────────────────────────────────────────────────────
with tab2:
    st.subheader("Trajectory Preview (first 2000 points)")
    n_preview = min(2000, len(x_arr))
    fig2, ax2 = plt.subplots(figsize=(6, 6))
    sc = ax2.scatter(x_arr[:n_preview], y_arr[:n_preview],
                     c=t_arr[:n_preview] * 1000, cmap="plasma", s=1, alpha=0.7)
    import matplotlib.patches as mpatches
    rect_patch = mpatches.Rectangle(
        (params["aperture_xL_mm"], params["aperture_yB_mm"]),
        params["aperture_xR_mm"] - params["aperture_xL_mm"],
        params["aperture_yT_mm"] - params["aperture_yB_mm"],
        linewidth=2, edgecolor="white", facecolor="none", linestyle="--",
    )
    ax2.add_patch(rect_patch)
    fig2.colorbar(sc, ax=ax2, label="Time (ms)")
    ax2.set_xlabel("X (mm)")
    ax2.set_ylabel("Y (mm)")
    ax2.set_title(f"Pattern: {pattern}")
    ax2.set_facecolor("#111")
    fig2.patch.set_facecolor("#111")
    ax2.tick_params(colors="white")
    ax2.xaxis.label.set_color("white")
    ax2.yaxis.label.set_color("white")
    ax2.title.set_color("white")
    st.pyplot(fig2)
    plt.close(fig2)

    if st.button("Generate & Download GIF"):
        with st.spinner("Rendering animation..."):
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".gif", delete=False) as tmp:
                gif_path = tmp.name
            ani = animate_trajectory(x_arr, y_arr, t_arr, aperture_rect=aperture_rect, save_path=gif_path)
            with open(gif_path, "rb") as f:
                gif_bytes = f.read()
            os.unlink(gif_path)
        st.download_button("Download GIF", gif_bytes, file_name="trajectory.gif", mime="image/gif")

# ── Tab 3: Metrics ───────────────────────────────────────────────────────────
with tab3:
    st.subheader("All Metrics")
    import pandas as pd
    metric_display = {
        "Flatness (%)": f"{metrics['flatness_pct']:.3f}",
        "RMS deviation (%)": f"{metrics['rms_pct']:.3f}",
        "Max/Min ratio": f"{metrics['max_min_ratio']:.4f}",
        "Pinch (%)": f"{metrics['pinch_pct']:.3f}",
        "Dwell mean (s/bin)": f"{metrics['dwell_mean']:.4e}",
        "Dwell std (s/bin)": f"{metrics['dwell_std']:.4e}",
        "Dwell peak/min ratio": f"{metrics['dwell_peak_min_ratio']:.3f}",
        "Characteristic τ (ms)": f"{metrics['tau_ms']:.4f}",
        "Diffusion length (μm)": f"{metrics['diffusion_length_um']:.4f}",
        "Steady state": str(metrics['steady_state']),
        "FWHM/spot rule pass": str(metrics['fwhm_spot_pass']),
        "Spot spacing (mm)": f"{metrics['spot_spacing_mm']:.4f}",
    }
    df = pd.DataFrame(list(metric_display.items()), columns=["Metric", "Value"])
    st.dataframe(df, use_container_width=True, hide_index=True)

    st.markdown(
        f"**Characteristic pulse duration τ = {metrics['tau_ms']:.4f} ms** — "
        f"time the beam illuminates one FWHM-width spot. "
        f"**Diffusion length L = {metrics['diffusion_length_um']:.2f} μm** — "
        f"how far an interstitial diffuses during τ. "
        f"Both should be small relative to the damage cascade spacing."
    )

    mask = _aperture_mask_from_edges(xe, ye, *aperture_rect)
    fig3 = plot_dwell_hist(rho, mask)
    st.pyplot(fig3)
    plt.close(fig3)

# ── Tab 4: Optimizer ─────────────────────────────────────────────────────────
with tab4:
    st.subheader("Grid Search — Objective Landscape")
    n_grid = st.slider("Grid resolution", 5, 20, 10, 1)
    if st.button("Run Grid Search"):
        with st.spinner("Evaluating grid..."):
            fx_vals, fy_vals, J_grid = grid_search(params, n_fx=n_grid, n_fy=n_grid)
        fig4, ax4 = plt.subplots(figsize=(7, 5))
        pcm4 = ax4.pcolormesh(fy_vals, fx_vals, J_grid, cmap="viridis_r", shading="auto")
        fig4.colorbar(pcm4, ax=ax4, label="Objective J")
        ax4.set_xlabel("f_y (Hz)")
        ax4.set_ylabel("f_x (Hz)")
        ax4.set_xscale("log")
        ax4.set_yscale("log")
        ax4.set_title("Objective Landscape (lower = better)")
        st.pyplot(fig4)
        plt.close(fig4)
        best_idx = np.unravel_index(np.argmin(J_grid), J_grid.shape)
        st.info(f"Grid best: f_x={fx_vals[best_idx[0]]:.0f} Hz, f_y={fy_vals[best_idx[1]]:.0f} Hz, J={J_grid[best_idx]:.3f}")

    st.divider()
    st.subheader("Differential Evolution Optimizer")
    st.caption("Optimizes [f_x, f_y, X-overscan, Y-overscan] jointly.")
    if st.button("Run Optimizer"):
        bounds = [(500, 10000), (1, 500), (1.0, 1.5), (1.0, 1.5)]
        with st.spinner("Running differential evolution (this may take 1–5 minutes)..."):
            result = run_optimizer(bounds, params)
        fx_opt, fy_opt, ax_f_opt, ay_f_opt = result.x
        st.success(f"Optimizer converged! J = {result.fun:.4f}")
        opt_cols = st.columns(4)
        opt_cols[0].metric("f_x (Hz)", f"{fx_opt:.0f}")
        opt_cols[1].metric("f_y (Hz)", f"{fy_opt:.0f}")
        opt_cols[2].metric("X overscan", f"{ax_f_opt:.3f}×")
        opt_cols[3].metric("Y overscan", f"{ay_f_opt:.3f}×")

        if st.button("Apply Optimal Values to GUI"):
            st.session_state["fx_slider"] = int(round(fx_opt / 10) * 10)
            st.session_state["fy_slider"] = int(round(fy_opt))
            st.rerun()
