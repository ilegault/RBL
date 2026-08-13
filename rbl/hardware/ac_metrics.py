"""
ac_metrics.py
Noise-rejecting measurements on an EEL5000 monitor waveform.

WHY THIS EXISTS
---------------
The obvious way to characterise an AC drive is peak and RMS over the collect
window.  Both are wrong for the current monitor on this rig, in different ways:

  MEAN is ~0 by construction.  A symmetric drive draws as much current one way
  as the other, so the window mean of the CURRENT monitor sits at zero however
  hard the amplifier is working.  Anything reporting mean current during an AC
  sweep is reporting nothing.

  PEAK is noise.  Peak over a collect window is a single sample out of ~100 000
  and can only be biased upward.  Measured against this rig's ~1.4 mA rms
  current-monitor noise floor, a peak-based current reading came out +181% high
  on a synthetic 2.4 mA triangle drive.

  RMS is closer, but still counts every noise sample: noise adds in quadrature,
  so RMS is inflated whenever the signal is not comfortably above the floor —
  which, at the low end of an amplitude ladder, it never is.

The drive frequency is KNOWN (the runner commanded it), so the honest
measurement is to ask what the monitor is doing AT THAT FREQUENCY and ignore
everything else.  That is a single-bin DFT — a lock-in amplifier in software.
Noise is rejected in proportion to the ratio of the measurement bandwidth to
the analysis bandwidth, which here is enormous: the same synthetic that biased
peak by +181% biases the fundamental by +0.2%.

WHY THE FUNDAMENTAL AND NOT THE WHOLE WAVEFORM
----------------------------------------------
On a capacitive load I = C dV/dt, and that relation holds SEPARATELY for every
harmonic.  So comparing the current's fundamental against the voltage's
fundamental is exact regardless of the shape being driven — the same arithmetic
works for a ramp, a sine or a square, with no per-shape correction factor.  The
peak-to-peak relation does not have that property: peak current is 2*pi*f*C*V
for a sine but 4*f*C*V for a triangle, a 57% difference, and using the wrong one
is exactly the kind of silent error this module exists to remove.

The fundamental is NOT the whole story for a non-sinusoidal drive — a square
current wave carries a third of its amplitude in harmonics — so `ac_metrics`
returns RMS and a robust peak alongside it.  Use the fundamental to measure a
level, the peak to reason about stress, and the ratio between them to tell
whether what you are looking at is signal or noise.

SCOPE
-----
Pure math.  No Qt, no hardware, no configuration.  Everything takes plain
arrays and returns plain floats, so it is testable without a LabJack and
reusable by anything that has samples and a frequency.
"""
import math

import numpy as np

# Percentile used for the robust peak.  High enough to sit at the true peak of
# a periodic signal (|x| is uniform on [0, A] for a triangle, so p99.9 = 0.999A;
# for a sine and a square it lands within 0.01% of A), low enough that
# displacing it takes 0.1% of the samples rather than one of them.
PEAK_PERCENTILE = 99.9

# Minimum whole cycles required before a fundamental estimate is offered. Below
# this the single-bin DFT has too little to average and leakage from nearby
# content dominates; returning NaN is more useful than a confident wrong number.
MIN_CYCLES = 4


def whole_cycle_samples(n_samples: int, sample_rate_hz: float,
                        freq_hz: float) -> int:
    """Largest prefix of *n_samples* spanning a whole number of cycles.

    Truncating to whole cycles is what makes the single-bin DFT exact.  A
    partial cycle at the end of the window is a discontinuity when the DFT
    implicitly wraps it, and that discontinuity smears energy across every bin
    (spectral leakage) — which would put drive energy into the bin being
    measured and noise into the drive's.  Windowing (Hann etc.) is the other
    way to suppress leakage, but it costs a known amplitude correction and
    still biases a short record; truncation costs at most one cycle out of
    hundreds and leaves the amplitude exact.
    """
    if freq_hz <= 0 or sample_rate_hz <= 0 or n_samples <= 0:
        return 0
    samples_per_cycle = sample_rate_hz / freq_hz
    cycles = int(n_samples // samples_per_cycle)
    if cycles < MIN_CYCLES:
        return 0
    return int(round(cycles * samples_per_cycle))


def fundamental(samples, sample_rate_hz: float, freq_hz: float):
    """(amplitude, phase_rad) of the component at *freq_hz*.

    Amplitude is the PEAK amplitude of that sinusoid, in the same units as
    `samples` — so a pure 2 V sine returns 2.0, not its 1.414 V RMS.  Peak is
    the more useful convention here because the quantities it gets compared
    against (commanded amplitude, the ladder's rungs, the amplifier's rating)
    are all peak quantities.

    Phase is referenced to cos(2*pi*f*t) at the first retained sample, and is
    only meaningful when compared against another channel's phase from the SAME
    window — which is the point: the current should lead the voltage by 90
    degrees on a capacitive load, and does not when something is wrong.

    Returns (nan, nan) when there is not enough data to be honest about.
    """
    x = np.asarray(samples, dtype=float)
    m = whole_cycle_samples(x.size, sample_rate_hz, freq_hz)
    if m == 0:
        return float("nan"), float("nan")
    seg = x[:m]
    if not np.all(np.isfinite(seg)):
        seg = seg[np.isfinite(seg)]
        if seg.size < MIN_CYCLES:
            return float("nan"), float("nan")
        m = seg.size
    k = np.arange(m, dtype=float)
    # 2/m normalisation puts the result in PEAK amplitude of a real sinusoid.
    coeff = (2.0 / m) * np.dot(seg, np.exp(-2j * np.pi * freq_hz * k / sample_rate_hz))
    return float(abs(coeff)), float(np.angle(coeff))


def ac_metrics(samples, sample_rate_hz: float, freq_hz: float) -> dict:
    """Full measurement set for one monitor over one collect window.

    Keys, all in the input's units (monitor volts for this rig):

        rms          sqrt(mean(x^2)) — RMS about ZERO, the true RMS.  Includes
                     the DC term, unlike a standard deviation.
        mean         DC level.  ~0 for a symmetric AC drive; the whole
                     measurement on a DC setpoint.
        peak         robust peak: the PEAK_PERCENTILE percentile of |x|.
        peak_abs     absolute worst sample.  Kept because after an interlock
                     trip the single worst excursion is the question, even
                     though it is the wrong statistic for measuring a level.
        fund_amp     peak amplitude at freq_hz — the noise-rejected number.
        fund_phase   phase at freq_hz, radians.
        crest        peak / rms.  A shape fingerprint: 1.41 sine, 1.73
                     triangle, 1.00 square.  Well above the expected value
                     means the "peak" is noise or transients, not the drive.
        fund_ratio   fund_amp / peak.  How much of the excursion is actually at
                     the drive frequency.  Near 1 means what you are measuring
                     is the drive; much below 1 means broadband noise or
                     harmonics dominate and the peak should not be trusted as
                     an amplitude.
        n_samples    samples considered.
        n_cycles     whole cycles the fundamental estimate used (0 if none).

    freq_hz <= 0 (a DC setpoint) is allowed: the fundamental fields come back
    NaN and everything else is still computed, so one code path serves both
    modes.
    """
    x = np.asarray(samples, dtype=float)
    nan = float("nan")
    if x.size == 0:
        return {"rms": nan, "mean": nan, "peak": nan, "peak_abs": nan,
                "fund_amp": nan, "fund_phase": nan, "crest": nan,
                "fund_ratio": nan, "n_samples": 0, "n_cycles": 0}

    absx = np.abs(x)
    rms  = float(np.sqrt(np.mean(np.square(x))))
    peak = float(np.percentile(absx, PEAK_PERCENTILE))
    out = {
        "rms":       rms,
        "mean":      float(np.mean(x)),
        "peak":      peak,
        "peak_abs":  float(np.max(absx)),
        "crest":     (peak / rms) if rms > 0 else nan,
        "n_samples": int(x.size),
    }
    m = whole_cycle_samples(x.size, sample_rate_hz, freq_hz)
    if m:
        amp, phase = fundamental(x, sample_rate_hz, freq_hz)
        out["fund_amp"]   = amp
        out["fund_phase"] = phase
        out["fund_ratio"] = (amp / peak) if peak > 0 else nan
        out["n_cycles"]   = int(round(m * freq_hz / sample_rate_hz))
    else:
        out["fund_amp"] = out["fund_phase"] = out["fund_ratio"] = nan
        out["n_cycles"] = 0
    return out


def phase_difference_deg(phase_a_rad: float, phase_b_rad: float) -> float:
    """(a - b) wrapped to (-180, 180] degrees.

    Used to check the current monitor against the voltage monitor: a purely
    capacitive load puts the current 90 degrees AHEAD of the voltage.  A result
    drifting toward 0 means a resistive component (leakage, an arc path, a
    flashover starting); toward 180 means the two monitors are cross-wired.
    """
    if not (math.isfinite(phase_a_rad) and math.isfinite(phase_b_rad)):
        return float("nan")
    d = math.degrees(phase_a_rad - phase_b_rad)
    return (d + 180.0) % 360.0 - 180.0


# ---------------------------------------------------------------------------
# Self-test — no hardware, no Qt.  Run:  python -m rbl.hardware.ac_metrics
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    FS, F, A = 50_000.0, 250.0, 2.0
    n = int(FS * 2.0)
    t = np.arange(n) / FS
    sine = A * np.sin(2 * np.pi * F * t)
    tri  = A * (2 / np.pi) * np.arcsin(np.sin(2 * np.pi * F * t))
    squ  = A * np.sign(np.sin(2 * np.pi * F * t))

    print("=== ac_metrics self-test ===")

    # Fundamental amplitude against the analytic Fourier series.
    for name, sig, expect in (
        ("sine",     sine, A),                    # fundamental IS the signal
        ("triangle", tri,  A * 8 / np.pi ** 2),   # 8/pi^2 = 0.8106
        ("square",   squ,  A * 4 / np.pi),        # 4/pi   = 1.2732
    ):
        amp, _ = fundamental(sig, FS, F)
        assert abs(amp - expect) < 1e-3, f"{name}: {amp} != {expect}"
        print(f"  {name:<9} fundamental {amp:.4f}  (analytic {expect:.4f})  OK")

    # Crest factors identify the shape.
    for name, sig, expect in (("sine", sine, math.sqrt(2)),
                              ("triangle", tri, math.sqrt(3)),
                              ("square", squ, 1.0)):
        m = ac_metrics(sig, FS, F)
        assert abs(m["crest"] - expect) < 0.02, f"{name} crest {m['crest']}"
        print(f"  {name:<9} crest {m['crest']:.3f}  (expected {expect:.3f})  OK")

    # I = C dV/dt holds per-harmonic, so C recovers from the fundamentals for
    # EVERY shape — the property the whole module rests on.
    C = 1200e-12
    for name, v_kv in (("sine", sine), ("triangle", tri), ("square", squ)):
        if name == "square":
            continue   # ideal square has infinite dV/dt; not a physical drive
        i_ma = np.gradient(v_kv * 1000.0, 1 / FS) * C * 1e3
        v1, pv = fundamental(v_kv, FS, F)
        i1, pi_ = fundamental(i_ma, FS, F)
        c_pf = (i1 * 1e-3) / (2 * np.pi * F * (v1 * 1000.0)) * 1e12
        d = phase_difference_deg(pi_, pv)
        assert abs(c_pf - 1200.0) < 5.0, f"{name}: C={c_pf}"
        assert abs(d - 90.0) < 1.0, f"{name}: phase={d}"
        print(f"  {name:<9} C {c_pf:.0f} pF, I leads V by {d:+.1f} deg  OK")

    # Noise rejection: the reason this module exists.
    rng = np.random.default_rng(1)
    i_clean = np.gradient(tri * 1000.0, 1 / FS) * C * 1e3
    i_noisy = i_clean + rng.normal(0, 1.4, n)      # rig's measured floor
    pk_err = np.percentile(np.abs(i_noisy), PEAK_PERCENTILE) / \
             np.percentile(np.abs(i_clean), PEAK_PERCENTILE) - 1
    f_err = fundamental(i_noisy, FS, F)[0] / fundamental(i_clean, FS, F)[0] - 1
    print(f"  noise +1.4 mA rms -> peak error {pk_err * 100:+.1f}%, "
          f"fundamental error {f_err * 100:+.1f}%")
    assert abs(f_err) < 0.02 and abs(pk_err) > 0.5, "noise rejection regressed"

    # Degenerate inputs must not raise.
    assert math.isnan(ac_metrics([], FS, F)["rms"])
    assert ac_metrics(sine, FS, 0.0)["n_cycles"] == 0        # DC setpoint
    assert math.isnan(ac_metrics(sine, FS, 0.0)["fund_amp"])
    assert whole_cycle_samples(10, FS, F) == 0               # under MIN_CYCLES
    assert math.isnan(fundamental([1.0, 2.0], FS, F)[0])
    print("  degenerate inputs (empty, DC, too-short) handled  OK")

    print("\n[OK] ac_metrics self-test passed")
