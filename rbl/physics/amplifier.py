"""
EEL5000.20.100 amplifier model for the UW-IBL/MIBL raster scan analysis.

Signal chain: RIGOL DG1000Z (+-10 V) -> EEL5000 (x1000 gain, +-5 kV) -> NEC ES5 steerer plates
                                         ^ THE BOTTLENECK ^

Spec sheet (EEL5000.20.100):
    Large-signal bandwidth : > 10 kHz at -3 dB (no load)
    Small-signal bandwidth : > 35 kHz at -3 dB (no load)
    Slew rate              : > 300 V/us
    Gain                   : 1 V -> 1000 V

Physical model:
    First-order low-pass:  H(f) = 1 / (1 + j*f/f_3dB)
    Differential eq:       tau * dV_out/dt + V_out = V_in,  tau = 1/(2*pi*f_3dB)
    Slew limit (non-linear): |dV/dt| <= SR_max  (saturates above this)
"""
import numpy as np


# --- Unit conversion helpers -------------------------------------------------

def mm_to_kV(mm, kV_per_mm):
    """Convert sample-plane deflection (mm) to amplifier voltage (kV)."""
    return mm * kV_per_mm


def kV_to_mm(kV, kV_per_mm):
    """Convert amplifier voltage (kV) to sample-plane deflection (mm)."""
    return kV / kV_per_mm


# --- Core filter functions ---------------------------------------------------

def apply_lowpass_fft(signal, dt, f_3dB_hz):
    """First-order low-pass via FFT -- complex H, so phase is correct (not zero-phase).

    Args:
        signal       : 1D array (any units -- V, mm, anything)
        dt           : sample interval in seconds
        f_3dB_hz     : -3 dB cutoff frequency in Hz

    Returns:
        Filtered signal, same length and dtype as input.
    """
    signal = np.asarray(signal, dtype=float)
    if f_3dB_hz <= 0 or len(signal) < 2:
        return signal.copy()
    N = len(signal)
    freqs = np.fft.fftfreq(N, dt)
    H = 1.0 / (1.0 + 1j * freqs / f_3dB_hz)
    Y = np.fft.fft(signal) * H
    return np.real(np.fft.ifft(Y))


def apply_slew_limit(signal, dt, slew_max_V_per_s):
    """Clamp |dsignal/dt| <= slew_max. Conservative integrate-and-clip model.

    Args:
        signal           : 1D array in volts
        dt               : sample interval in seconds
        slew_max_V_per_s : max allowed |dV/dt| in V/s (NOT V/us)

    Returns:
        Slew-limited signal, same length as input.
    """
    signal = np.asarray(signal, dtype=float)
    if slew_max_V_per_s <= 0 or len(signal) < 2:
        return signal.copy()
    max_step = slew_max_V_per_s * dt  # max allowed change between adjacent samples
    out = np.empty_like(signal)
    out[0] = signal[0]
    diffs = np.diff(signal)
    clamped = np.clip(diffs, -max_step, max_step)
    out[1:] = signal[0] + np.cumsum(clamped)
    return out


# --- Full amplifier pipeline -------------------------------------------------

def apply_amplifier(t, x_mm, y_mm, params):
    """Pass an ideal (x, y) trajectory in mm through the EEL5000 model.

    Pipeline (per axis):
        mm -> kV -> V -> low-pass filter -> slew clamp -> V -> kV -> mm

    Y is also filtered for consistency. At typical Y frequencies (<500 Hz) the
    effect is negligible.

    Args:
        t      : time array, seconds, uniform sampling
        x_mm   : ideal X trajectory in mm
        y_mm   : ideal Y trajectory in mm
        params : dict, reads keys (all optional, with defaults):
            amplifier_bw_hz           default 10000.0
            amplifier_slew_V_per_us   default 300.0
            kV_per_mm                 default 0.368

    Returns:
        (x_mm_realistic, y_mm_realistic) -- sample-plane trajectory the beam actually follows.
    """
    t    = np.asarray(t,    dtype=float)
    x_mm = np.asarray(x_mm, dtype=float)
    y_mm = np.asarray(y_mm, dtype=float)
    if len(t) < 2:
        return x_mm.copy(), y_mm.copy()

    dt            = t[1] - t[0]
    f_3dB         = params.get("amplifier_bw_hz",         10000.0)
    slew_V_per_us = params.get("amplifier_slew_V_per_us", 300.0)
    kV_per_mm     = params.get("kV_per_mm",               0.368)
    slew_V_per_s  = slew_V_per_us * 1.0e6   # V/us -> V/s

    # mm -> V (work in volts so SR units are V/s)
    x_V = mm_to_kV(x_mm, kV_per_mm) * 1000.0
    y_V = mm_to_kV(y_mm, kV_per_mm) * 1000.0

    # Bandwidth filter
    x_V = apply_lowpass_fft(x_V, dt, f_3dB)
    y_V = apply_lowpass_fft(y_V, dt, f_3dB)

    # Slew clamp
    x_V = apply_slew_limit(x_V, dt, slew_V_per_s)
    y_V = apply_slew_limit(y_V, dt, slew_V_per_s)

    # V -> mm
    x_mm_out = kV_to_mm(x_V / 1000.0, kV_per_mm)
    y_mm_out = kV_to_mm(y_V / 1000.0, kV_per_mm)
    return x_mm_out, y_mm_out


# --- Convenience: required vs available slew ---------------------------------

def required_slew_rate_V_per_s(x_mm, t, kV_per_mm):
    """Peak |dV/dt| required to reproduce the ideal trajectory (in V/s)."""
    x_mm = np.asarray(x_mm, dtype=float)
    t    = np.asarray(t,    dtype=float)
    if len(t) < 2:
        return 0.0
    V  = x_mm * kV_per_mm * 1000.0
    dt = t[1] - t[0]
    return float(np.max(np.abs(np.diff(V) / dt)))


# --- Self-test (run with: python amplifier.py) --------------------------------

def _self_test():
    p = {"amplifier_bw_hz": 10000.0, "amplifier_slew_V_per_us": 300.0, "kV_per_mm": 0.368}

    # 1. DC passes through unchanged
    t  = np.linspace(0, 0.1, 10000, endpoint=False)
    dc = np.full_like(t, 5.0)
    x_out, _ = apply_amplifier(t, dc, dc * 0, p)
    assert np.allclose(x_out, dc, atol=0.05), "DC test failed"
    print("[PASS] DC signal passes through")

    # 2. 1 kHz (well below 10 kHz BW) -- amplitude retained >99%
    n = 100000
    t = np.linspace(0, 0.1, n, endpoint=False)
    x = 5.0 * np.sin(2 * np.pi * 1000.0 * t)
    x_out, _ = apply_amplifier(t, x, np.zeros_like(x), p)
    ratio = x_out.std() / x.std()
    assert 0.99 < ratio < 1.01, f"1 kHz amplitude ratio = {ratio:.4f}"
    print(f"[PASS] 1 kHz signal: amplitude ratio {ratio:.4f}")

    # 3. 10 kHz signal -- at -3 dB -> ratio ~0.707
    x = 5.0 * np.sin(2 * np.pi * 10000.0 * t)
    x_out, _ = apply_amplifier(t, x, np.zeros_like(x), p)
    ratio = x_out.std() / x.std()
    assert 0.68 < ratio < 0.73, f"10 kHz amplitude ratio = {ratio:.4f}, expected ~0.707"
    print(f"[PASS] 10 kHz signal: amplitude ratio {ratio:.4f} (theory 0.707)")

    # 4. Slew limit respected on a step
    sig_V = np.concatenate([np.zeros(500), np.full(9500, 5000.0)])  # 5 kV step
    out_V = apply_slew_limit(sig_V, dt=1e-6, slew_max_V_per_s=300e6)  # 300 V/us
    peak_slope = np.max(np.abs(np.diff(out_V)) / 1e-6)
    assert peak_slope <= 300e6 * 1.001, f"Slew limit exceeded: {peak_slope:.2e} V/s"
    print(f"[PASS] Slew clamp respects 300 V/us (peak slope {peak_slope:.2e} V/s)")

    print("\namplifier.py self-test: ALL PASS")


if __name__ == "__main__":
    _self_test()
