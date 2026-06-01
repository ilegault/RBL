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

    # --- Amplifier model (EEL5000.20.100 large-signal mode) ---
    "simulate_amplifier":         True,    # global toggle: include amplifier physics
    "amplifier_slew_V_per_us":    300.0,   # EEL5000 slew rate, V/us
    "amplifier_gain_V_per_V":     1000.0,  # 1 V input -> 1000 V output (display only)
    "kV_per_mm":                  0.368,   # Hirst CSV: 9.5 kV -> 25.79 mm @ 3 MeV protons

    # ASTM flatness target
    "flatness_target_pct": 10.0,

    # Optimizer weights
    "w1": 1.0,
    "w2": 0.5,
    "w3": 0.1,
    "w4": 1.0,
    "w5": 0.1,

    # --- Extra optimizer weight for triangularity (waveform-fidelity) ---
    "w6": 0.5,    # penalty on (1 - triangularity_score); 0 disables

    # --- New optimizer weight (pixel revisit / FDRT off-time) ---
    "w7":                         2.0,    # penalty for max_pixel_off_time > tau_recomb

    # --- Auto-scaled T_total for the optimizer (per-trial; doesn't change user GUI) ---
    "optimizer_min_slow_cycles":  5.0,    # require >= this many slow-axis cycles per trial
    "optimizer_T_max_ms":         500.0,  # hard cap on T_total inside optimizer (don't blow up runtime)

    # --- Hard lower bound on fy for the optimizer (Hz). 50 Hz = 20 ms frame period ---
    # --- already much faster than typical tau_recomb in real samples. ---
    "optimizer_fy_min_hz":        50.0,
}
