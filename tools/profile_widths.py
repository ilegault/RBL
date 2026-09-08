#!/usr/bin/env python3
"""
Measure FWHM, FWTM and FW(1/e^2) on the waveforms saved by the RBL profiler.

    python rbl_profile_widths.py profile_20260826T130221.waveforms.csv
    python rbl_profile_widths.py <file>.waveforms.csv --mm-per-ms 2.19
    python rbl_profile_widths.py <file>.waveforms.csv --mm-per-rev 120

Input is the .waveforms.csv the profile logger writes: one row per shot,
first field a label, the rest the saved samples in volts.

Nothing here fits a curve to get a width.  Each width is measured by walking
out from the apex to the first sample below the level and interpolating
between that sample and its neighbour - so a non-Gaussian peak is reported as
it actually is, which is the whole point of measuring three levels.

The Gaussian fit at the end is only there as a comparison; scipy is optional.
"""
import csv
import math
import sys

import numpy as np

# --- the trace ------------------------------------------------------------
# The scope preamble on this run said XINCR = 20 us with 2500 points (a 50 ms
# record) and the logger saved 500 samples per shot, so it decimates by 5.
# If that ever changes, change this - every width below scales with it.
XINCR_SECONDS = 20e-6
SCOPE_POINTS = 2500

# The two peaks in one record are X then Y.  Split them at a sample index in
# the flat gap between them.
SPLIT_FRACTION = 0.5

LEVELS = {"FWHM": 0.5, "FWTM": 0.1, "FW1/e^2": math.exp(-2)}

# Millimetres per second of trace.  This has to come from a FIDUCIAL trace -
# the calibration marks the BPM head generates, 60 mm apart on a BPM80 - taken
# with the controller's output selector on fiducials.  Pass it with
# --mm-per-ms; without it every width below stays in milliseconds, which is
# what was actually measured.
#
# Do NOT calibrate off the beam's own X and Y peaks.  Their separation is
# 60 mm only when the beam is centred in both planes: the X apex sits where
# the beam is in X and the Y apex where it is in Y, so the separation moves
# with the beam.  This script prints that separation per shot so you can see
# it move.  On profile_20260826T130221 it runs 22.2 to 27.5 ms across six
# shots - a ruler that changes length by 24 % between shots.
SPACING_MM = 60.0

# A Gaussian's three widths are locked to each other.  Measured ratios that
# miss these say the beam is not Gaussian, and by how much.
GAUSS_FWTM_OVER_FWHM = math.sqrt(math.log(10) / math.log(2))   # 1.8226
GAUSS_FW1E2_OVER_FWHM = 2 / math.sqrt(2 * math.log(2))         # 1.6986


def load(path):
    """-> (labels, array of shape (n_shots, n_samples), dt in seconds)"""
    rows = [r for r in csv.reader(open(path)) if r]
    labels = [r[0] for r in rows]
    data = np.array([[float(v) for v in r[1:]] for r in rows])
    dt = XINCR_SECONDS * SCOPE_POINTS / data.shape[1]
    return labels, data, dt


def crossing(y, apex, step, level):
    """Index, fractional, where y drops through `level` walking from `apex`."""
    i = apex
    while 0 < i < len(y) - 1:
        j = i + step
        if y[j] <= level:
            if y[i] == y[j]:
                return float(j)
            return i + step * (y[i] - level) / (y[i] - y[j])
        i = j
    return float("nan")


def width(y, apex, level, dt):
    left = crossing(y, apex, -1, level)
    right = crossing(y, apex, +1, level)
    return (right - left) * dt


def apexes(y, split):
    """Sample index of the tallest point each side of the split."""
    return int(np.argmax(y[:split])), int(split + np.argmax(y[split:]))


def all_apexes(y, min_frac=0.30, min_gap=None):
    """Every peak above min_frac of the tallest, no two closer than min_gap."""
    min_gap = min_gap or len(y) // 10
    found = []
    for i in np.argsort(y)[::-1]:
        if y[i] < min_frac * y.max():
            break
        if all(abs(i - a) > min_gap for a in found):
            found.append(int(i))
    return sorted(found)


def rotation_period(y, dt):
    """
    X apex to the NEXT X apex - one revolution of the wire.

    This is the only per-shot ruler in the trace.  Both X apexes move together
    when the beam moves, so the beam cancels out of the subtraction; the X-to-Y
    gap does not have that property and is a beam POSITION readout, not a scale.

    Returns (period_seconds, note).  A record shorter than one revolution has
    only the X and Y peaks in it and gets None - say so, never fall back.
    """
    p = all_apexes(y)
    if len(p) < 3:
        return None, f"only {len(p)} peaks in the record - shorter than one revolution"
    x_to_x = (p[2] - p[0]) * dt
    if len(p) < 4:
        return x_to_x, "from X to X only - no second Y peak to cross-check against"
    y_to_y = (p[3] - p[1]) * dt
    disagree = abs(x_to_x - y_to_y) / x_to_x
    note = f"X-X {x_to_x*1e3:.3f} ms vs Y-Y {y_to_y*1e3:.3f} ms, {disagree:.2%} apart"
    if disagree > 0.02:
        note += "  <-- they should agree; do not trust this shot"
    return (x_to_x + y_to_y) / 2, note


def gaussian_sigma(y, apex, dt, fwhm):
    """Least-squares sigma over the peak down to 5 % of its height. None if no scipy."""
    try:
        from scipy.optimize import curve_fit
    except ImportError:
        return None
    a = y[apex]
    lo = int(crossing(y, apex, -1, 0.05 * a))
    hi = int(math.ceil(crossing(y, apex, +1, 0.05 * a)))
    x = np.arange(lo, hi + 1) * dt
    def f(x, A, mu, s, c):
        return A * np.exp(-0.5 * ((x - mu) / s) ** 2) + c
    try:
        p, _ = curve_fit(f, x, y[lo:hi + 1], p0=[a, apex * dt, fwhm / 2.3548, 0.0])
    except Exception:
        return None
    resid = y[lo:hi + 1] - f(x, *p)
    r2 = 1 - resid.var() / y[lo:hi + 1].var()
    return abs(p[2]), r2


def measure(trace, dt):
    """One shot -> dict per peak.  Baseline is the median: the peaks are narrow."""
    baseline = float(np.median(trace))
    y = trace - baseline
    railed = int((trace >= trace.max() - 1e-9).sum()) > 3
    split = int(len(y) * SPLIT_FRACTION)
    out = {"baseline": baseline, "railed": railed, "peaks": []}
    for axis, apex in zip("XY", apexes(y, split)):
        amp = float(y[apex])
        p = {"axis": axis, "apex_ms": apex * dt * 1e3, "amp_V": amp}
        for name, frac in LEVELS.items():
            p[name] = width(y, apex, frac * amp, dt)
        ratio = p["FWTM"] / p["FWHM"]
        p["FWTM/FWHM"] = ratio
        p["FW1e2/FWHM"] = p["FW1/e^2"] / p["FWHM"]
        # exp(-ln2 * |2x/FWHM|^n):  n = 2 Gaussian, > 2 flat top, < 2 heavy tails
        p["order_n"] = (math.log(math.log(10) / math.log(2)) / math.log(ratio)
                        if ratio > 1.001 else float("nan"))
        p["FWTM_error_if_gaussian"] = ratio / GAUSS_FWTM_OVER_FWHM - 1
        fit = gaussian_sigma(y, apex, dt, p["FWHM"])
        p["fit_sigma"], p["fit_r2"] = fit if fit else (float("nan"), float("nan"))
        out["peaks"].append(p)
    out["period"], out["period_note"] = rotation_period(y, dt)
    out["x_to_y"] = (out["peaks"][1]["apex_ms"] - out["peaks"][0]["apex_ms"])
    return out


def main(path, mm_per_ms=None, mm_per_rev=None):
    labels, data, dt = load(path)
    print(f"{path}\n{data.shape[0]} shots x {data.shape[1]} samples, "
          f"{dt*1e3:.4f} ms per sample, {data.shape[1]*dt*1e3:.1f} ms record\n")
    unit = "mm" if (mm_per_ms or mm_per_rev) else "ms"
    if mm_per_rev:
        print(f"calibration: {mm_per_rev} mm per revolution, scale re-measured every shot")
    elif mm_per_ms:
        print(f"calibration: {mm_per_ms:.4f} mm/ms, fixed")
    else:
        print("calibration: none - widths are in milliseconds")
    k_ = mm_per_ms * 1e3 if mm_per_ms else 1e3     # seconds -> mm, or -> ms
    print()
    print("shot  ax   apex     amp    FWHM    FWTM  FW1/e2   FWTM/  FW1e2/  order   fit    fit")
    print(f"               ms       V   {unit:>5}   {unit:>5}   {unit:>5}    FWHM    FWHM"
          "      n  sig ms    R2")
    seps, notes = [], []
    for k, (label, trace) in enumerate(zip(labels, data)):
        m = measure(trace, dt)
        seps.append((m["x_to_y"], m["railed"]))
        notes.append(m["period_note"])
        # per-shot scale from this shot's own period, when that is the ruler
        if mm_per_rev and m["period"]:
            k_ = mm_per_rev / m["period"]              # mm per second of trace
        elif mm_per_rev:
            k_ = float("nan")                          # no period, no millimetres
        for p in m["peaks"]:
            print(f"{k+1:>4}  {p['axis']:>2} {p['apex_ms']:>7.2f} {p['amp_V']:>7.3f}"
                  f" {p['FWHM']*k_:>7.4f} {p['FWTM']*k_:>7.4f} {p['FW1/e^2']*k_:>7.4f}"
                  f" {p['FWTM/FWHM']:>7.3f} {p['FW1e2/FWHM']:>7.3f} {p['order_n']:>6.2f}"
                  f" {p['fit_sigma']*1e3:>7.4f} {p['fit_r2']:>6.4f}"
                  + ("   RAILED - no width here is valid" if m["railed"] else ""))

    print("\nRotation period - the only per-shot ruler in the trace:")
    for k, n in enumerate(notes):
        print(f"   shot {k+1}: {n}")
    print("   A 50 ms record at 5 ms/div holds one X and one Y peak and stops just short of")
    print("   the next revolution. Go to 10 ms/div and the period becomes measurable every shot.")

    print("\nIf you used the X-to-Y peak separation as the 60 mm ruler instead:")
    scales = [SPACING_MM / s for s, _ in seps]
    for k, ((sep, railed), sc) in enumerate(zip(seps, scales)):
        print(f"   shot {k+1}: {sep:6.2f} ms apart -> {sc:6.3f} mm/ms"
              + ("   (railed shot)" if railed else ""))
    print(f"   widest / narrowest = {max(scales)/min(scales)-1:.1%}. A ruler does not change"
          " length between shots;\n   these two apexes sit where the BEAM is, "
          "not where the marks are.")
    print(f"\nGaussian would give FWTM/FWHM = {GAUSS_FWTM_OVER_FWHM:.4f}, "
          f"FW1e2/FWHM = {GAUSS_FW1E2_OVER_FWHM:.4f}, order n = 2.")
    print("Below 1.8226: flat-topped core.  Above: heavy tails or an unresolved second component.")


if __name__ == "__main__":
    args = sys.argv[1:]
    scale = None
    per_rev = None
    if "--mm-per-ms" in args:
        i = args.index("--mm-per-ms")
        scale = float(args[i + 1])
        del args[i:i + 2]
    if "--mm-per-rev" in args:
        i = args.index("--mm-per-rev")
        per_rev = float(args[i + 1])
        del args[i:i + 2]
    if len(args) != 1:
        sys.exit(__doc__)
    main(args[0], scale, per_rev)
