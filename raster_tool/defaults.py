DEFAULTS = {
    # Beam
    "fwhm_x_mm": 2.0,
    "fwhm_y_mm": 2.0,

    # Aperture (slit-defined rectangular window, symmetric about origin)
    "aperture_xL_mm": -5.0,
    "aperture_xR_mm":  5.0,
    "aperture_yB_mm": -7.0,
    "aperture_yT_mm":  7.0,

    # Scan amplitudes (half-amplitude = distance from center to edge of scan)
    "ax_mm": 6.5,   # X half-amplitude (aperture_half × 1.30 = 30% overscan)
    "ay_mm": 9.1,   # Y half-amplitude

    # Raster frequencies
    "fx_hz": 2061.0,
    "fy_hz": 255.0,

    # Scan pattern
    "pattern": "classic",  # classic | alt_axes | lissajous | spiral | sinusoidal | wobble

    # Optional Lissajous phase
    "lissajous_phase_deg": 0.0,

    # Time
    "T_total_ms": 100.0,
    "n_time_samples": 50000,

    # Dose grid
    "grid_nx": 256,
    "grid_ny": 256,

    # Physics / FDRT
    "tau_recomb_ms": 1.0,
    "D_interstitial_m2s": 1e-9,
    "fdrt_threshold_hz": 500.0,
    "amplifier_bw_hz": 10000.0,

    # ASTM flatness target
    "flatness_target_pct": 10.0,

    # Optimizer weights
    "w1": 1.0,
    "w2": 0.5,
    "w3": 0.1,
    "w4": 1.0,
    "w5": 0.1,
}
