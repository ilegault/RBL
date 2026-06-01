import numpy as np
from scipy.signal import sawtooth

from rbl.physics.amplifier import apply_amplifier


def classic_raster(fx, fy, ax, ay, T_s, n_samples):
    """Triangle on X (fast), sawtooth/ramp on Y (slow). MIBL canonical pattern."""
    t = np.linspace(0, T_s, n_samples, endpoint=False)
    x = ax * sawtooth(2 * np.pi * fx * t, width=0.5)
    y = ay * sawtooth(2 * np.pi * fy * t, width=1.0)
    return t, x, y


def alternating_axes(fx, fy, ax, ay, T_s, n_samples):
    """Every slow-axis frame, swap which axis is fast."""
    t = np.linspace(0, T_s, n_samples, endpoint=False)
    frame_idx = np.floor(t * fy).astype(int)
    x = np.where(
        frame_idx % 2 == 1,
        ax * sawtooth(2 * np.pi * fx * t, width=0.5),   # odd: x is fast
        ax * sawtooth(2 * np.pi * fy * t, width=1.0),   # even: x is slow
    )
    y = np.where(
        frame_idx % 2 == 1,
        ay * sawtooth(2 * np.pi * fy * t, width=1.0),   # odd: y is slow
        ay * sawtooth(2 * np.pi * fx * t, width=0.5),   # even: y is fast
    )
    return t, x, y


def lissajous(fx, fy, ax, ay, phase_deg, T_s, n_samples):
    """Lissajous figure: sine on both axes with optional phase offset."""
    t = np.linspace(0, T_s, n_samples, endpoint=False)
    phase_rad = phase_deg * np.pi / 180.0
    x = ax * np.sin(2 * np.pi * fx * t)
    y = ay * np.sin(2 * np.pi * fy * t + phase_rad)
    return t, x, y


def spiral(f_angular, r_max, T_s, n_samples):
    """Outward spiral: radius grows linearly, angle spins at f_angular."""
    t = np.linspace(0, T_s, n_samples, endpoint=False)
    r = r_max * t / T_s
    x = r * np.cos(2 * np.pi * f_angular * t)
    y = r * np.sin(2 * np.pi * f_angular * t)
    return t, x, y


def sinusoidal_raster(fx, fy, ax, ay, T_s, n_samples):
    """Sine on fast axis, ramp on slow axis."""
    t = np.linspace(0, T_s, n_samples, endpoint=False)
    x = ax * np.sin(2 * np.pi * fx * t)
    y = ay * sawtooth(2 * np.pi * fy * t, width=1.0)
    return t, x, y


def wobbled_defocus(fx, fy, ax, ay, T_s, n_samples):
    """Small wobble around defocused (stationary center) position."""
    t = np.linspace(0, T_s, n_samples, endpoint=False)
    x = ax * np.sin(2 * np.pi * fx * t)
    y = ay * np.sin(2 * np.pi * fy * t)
    return t, x, y


def get_pattern(name, params):
    """Dispatch helper: return (t, x, y) for the named pattern using params dict."""
    T_s = params["T_total_ms"] * 1e-3
    n = params["n_time_samples"]
    fx = params["fx_hz"]
    fy = params["fy_hz"]
    ax = params["ax_mm"]
    ay = params["ay_mm"]

    if name == "classic":
        return classic_raster(fx, fy, ax, ay, T_s, n)
    elif name == "alt_axes":
        return alternating_axes(fx, fy, ax, ay, T_s, n)
    elif name == "lissajous":
        return lissajous(fx, fy, ax, ay, params["lissajous_phase_deg"], T_s, n)
    elif name == "spiral":
        r_max = max(ax, ay)
        return spiral(fy, r_max, T_s, n)
    elif name == "sinusoidal":
        return sinusoidal_raster(fx, fy, ax, ay, T_s, n)
    elif name == "wobble":
        return wobbled_defocus(fx, fy, ax, ay, T_s, n)
    else:
        raise ValueError(f"Unknown pattern: {name}")


def get_realistic_trajectory(params):
    """Return (t, x, y) -- ideal trajectory, optionally amplifier-filtered.

    Single chokepoint for trajectory generation. Every consumer in the codebase
    should call THIS instead of get_pattern() directly, so the global
    `simulate_amplifier` toggle takes effect everywhere consistently.

    Reads params["simulate_amplifier"] (default True). When False, behaves
    identically to get_pattern() -- used for ideal/reference plots.
    """
    t, x, y = get_pattern(params["pattern"], params)
    if params.get("simulate_amplifier", True):
        x, y = apply_amplifier(t, x, y, params)
    return t, x, y
