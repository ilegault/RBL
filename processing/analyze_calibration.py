#!/usr/bin/env python3
"""
Analyze an HV amplifier calibration CSV produced by the RBL Calibration tab.

Usage:
    python analyze_calibration.py cal_20260804T111951.csv

This is the standalone version of the deferred rbla Phase 9 analysis tab.
Everything here is read-only: it never commands hardware and never writes
correction factors. It reports.

--------------------------------------------------------------------------
THE ONE THING TO UNDERSTAND ABOUT THE FILE FORMAT
--------------------------------------------------------------------------
The CSV is in LONG format. At every setpoint, ALL EIGHT amplifier monitors
are recorded -- not just the one being driven. So for each setpoint you get:

    1 row  driven channel, voltage    <-- the calibration curve
    1 row  driven channel, current
    3 rows undriven channels, voltage <-- crosstalk data
    3 rows undriven channels, current

That means 7 of every 8 rows are NOT the calibration curve. If you open the
raw file and scan down the amp_label column, you will see (for example) X+
appearing at commanded_kv = 3.4 during a sweep of Y-. X+ was sitting at zero
the whole time; commanded_kv describes what the DRIVEN channel was asked for,
not what this row's channel was asked for. Read linearly, that looks like the
file skips around. It doesn't.

The filter that recovers the calibration curve is:

    df[(df.kind == 'voltage') & (df.driven_amp == df.amp_label)]

Everything else is crosstalk and current-draw data.
--------------------------------------------------------------------------
"""

import sys
import numpy as np
import pandas as pd

# Combined amp (0.5% FS) + monitor (0.1% FS) + DAQ uncertainty, volts at output.
# Deviations inside this band are noise, not findings.
UNCERTAINTY_V = 30.0

# EEL5000 output offset spec, volts DC.
AMP_OFFSET_SPEC_V = 2.0


def load(path):
    df = pd.read_csv(path)
    driven_v = df[(df.kind == "voltage") & (df.driven_amp == df.amp_label)].copy()
    driven_i = df[(df.kind == "current") & (df.driven_amp == df.amp_label)].copy()
    cross_v = df[(df.kind == "voltage") & (df.driven_amp != df.amp_label)].copy()
    return df, driven_v, driven_i, cross_v


def fits(driven_v):
    """Per-channel linear fit of measured kV against commanded kV."""
    out = {}
    for amp, g in driven_v.groupby("amp_label"):
        m = g.groupby("commanded_kv").converted_value.mean()
        x, y = m.index.values, m.values
        gain, offset = np.polyfit(x, y, 1)
        resid = y - (gain * x + offset)

        pos = m[m.index > 0]
        neg = m[m.index < 0]
        gain_pos = np.polyfit(pos.index.values, pos.values, 1)[0]
        gain_neg = np.polyfit(neg.index.values, neg.values, 1)[0]

        out[amp] = dict(
            gain=gain,
            offset_v=offset * 1000.0,
            max_resid_v=np.abs(resid).max() * 1000.0,
            rms_resid_v=float(np.sqrt((resid ** 2).mean())) * 1000.0,
            gain_pos=gain_pos,
            gain_neg=gain_neg,
            asym_pct=(gain_pos - gain_neg) / ((gain_pos + gain_neg) / 2) * 100.0,
            curve=m,
        )
    return out


def report(path):
    df, driven_v, driven_i, cross_v = load(path)

    print(f"\n{'=' * 72}")
    print(f"  {path}")
    print(f"{'=' * 72}")
    print(f"  {len(df):>6} total rows")
    print(f"  {len(driven_v):>6} driven-voltage rows  <-- the calibration curve")
    print(f"  {len(driven_i):>6} driven-current rows")
    print(f"  {len(cross_v) * 2:>6} undriven rows (crosstalk, V and I)")
    print(f"  run duration: {df.t_elapsed_s.max() / 60:.1f} min")
    print(f"  stream profile: {df.stream_profile.unique()[0]}")
    print(f"  commanded range: {df.commanded_kv.min():+.1f} .. "
          f"{df.commanded_kv.max():+.1f} kV")

    f = fits(driven_v)

    # ---- Gain, offset, linearity -----------------------------------------
    print(f"\n{'-' * 72}\n  GAIN / OFFSET / LINEARITY\n{'-' * 72}")
    print(f"  {'ch':<5}{'gain':>10}{'offset V':>11}{'max resid':>12}"
          f"{'rms resid':>11}{'+/- asym':>11}")
    for amp, r in f.items():
        print(f"  {amp:<5}{r['gain']:>10.5f}{r['offset_v']:>+11.2f}"
              f"{r['max_resid_v']:>11.2f} V{r['rms_resid_v']:>10.2f} V"
              f"{r['asym_pct']:>+10.2f}%")

    gains = np.array([r["gain"] for r in f.values()])
    print(f"\n  gain spread across channels: {(gains.max() - gains.min()) * 100:.4f}%")
    if (gains.max() - gains.min()) * 100 < 0.1 and abs(gains.mean() - 1) > 0.001:
        print("  NOTE: all four channels share nearly the same gain error.")
        print("        Four independent amplifiers do not converge on the same")
        print("        error by chance. This points at a COMMON-MODE cause")
        print("        (generator DC accuracy, T7 gain, or the assumed 1000 V/V")
        print("        monitor divider) rather than four amplifier faults.")

    # ---- Offsets, and what actually steers the beam ----------------------
    print(f"\n{'-' * 72}\n  OFFSETS: SINGLE-PLATE vs DIFFERENTIAL\n{'-' * 72}")
    print("  A single plate's offset shifts the pair's DC potential. It is the")
    print("  DIFFERENCE across a plate pair that steers the beam.\n")
    for a, b, label in (("X+", "X-", "X"), ("Y+", "Y-", "Y")):
        if a in f and b in f:
            diff = f[a]["offset_v"] - f[b]["offset_v"]
            print(f"  {label} pair: {a} {f[a]['offset_v']:+7.2f} V   "
                  f"{b} {f[b]['offset_v']:+7.2f} V   "
                  f"differential {diff:+7.2f} V")
    print(f"\n  EEL5000 output offset spec: +/-{AMP_OFFSET_SPEC_V:.0f} V")
    for amp, r in f.items():
        if abs(r["offset_v"]) > AMP_OFFSET_SPEC_V:
            print(f"    {amp}: {r['offset_v']:+.2f} V is "
                  f"{abs(r['offset_v']) / AMP_OFFSET_SPEC_V:.1f}x the spec")

    # ---- Hysteresis and repeatability ------------------------------------
    print(f"\n{'-' * 72}\n  HYSTERESIS AND REPEATABILITY\n{'-' * 72}")
    print(f"  {'ch':<5}{'up-down mean':>15}{'up-down max':>14}{'pass std':>13}")
    for amp, g in driven_v.groupby("amp_label"):
        p = g.pivot_table(index="commanded_kv", columns="pass_type",
                          values="converted_value")
        h = (p["up"] - p["down"]) * 1000
        s = p.std(axis=1) * 1000
        print(f"  {amp:<5}{h.mean():>+14.2f} V{h.abs().max():>13.2f} V"
              f"{s.median():>12.2f} V")

    # ---- Noise vs voltage (corona / partial discharge) -------------------
    print(f"\n{'-' * 72}\n  NOISE vs VOLTAGE  (corona / partial discharge check)\n{'-' * 72}")
    print("  A sharp rise in std at high |V| is the signature of discharge onset.\n")
    dv = driven_v.copy()
    dv["absk"] = dv.commanded_kv.abs()
    bins = pd.cut(dv.absk, [-0.01, 1, 2, 3, 4, 5.01])
    for rng, val in (dv.groupby(bins, observed=True).std_v.median() * 1000).items():
        print(f"    |V| {str(rng):<14} median std = {val:.3f} mV at monitor")

    # ---- Crosstalk --------------------------------------------------------
    print(f"\n{'-' * 72}\n  CROSSTALK  (volts induced on victim per kV on driver)\n{'-' * 72}")
    rows = []
    for (drv, vic), g in cross_v.groupby(["driven_amp", "amp_label"]):
        m = g.groupby("commanded_kv").converted_value.mean()
        slope = np.polyfit(m.index.values, m.values, 1)[0]
        rows.append((drv, vic, slope * 1000))
    ct = pd.DataFrame(rows, columns=["driven", "victim", "V_per_kV"])
    print(ct.pivot(index="driven", columns="victim", values="V_per_kV")
            .round(3).to_string())

    # ---- Current draw -----------------------------------------------------
    print(f"\n{'-' * 72}\n  CURRENT MONITOR on driven channel\n{'-' * 72}")
    print("  Caveat: current monitor accuracy is 1% of FS. Small readings here")
    print("  may be monitor offset rather than real load current. The USEFUL")
    print("  measurement is the DIFFERENCE between a disconnected run and an")
    print("  on-plates run -- offsets cancel in that subtraction.\n")
    for amp, g in driven_i.groupby("amp_label"):
        m = g.groupby("commanded_kv").converted_value.mean()
        lo, hi = m.index.min(), m.index.max()
        print(f"  {amp}: {m.loc[0.0]:+.4f} mA at 0 kV   "
              f"{m.loc[hi]:+.4f} at {hi:+.1f}   "
              f"{m.loc[lo]:+.4f} at {lo:+.1f}   span {m.max() - m.min():.4f} mA")

    # ---- Zero drift across the run ---------------------------------------
    print(f"\n{'-' * 72}\n  ZERO-POINT STABILITY ACROSS THE RUN\n{'-' * 72}")
    z = driven_v[driven_v.commanded_kv == 0.0]
    for amp, g in z.groupby("amp_label"):
        vals = g.converted_value.values * 1000
        print(f"  {amp}: {vals.mean():+7.2f} V mean, "
              f"{vals.max() - vals.min():.2f} V peak-to-peak over the run")

    # ---- Verdict ----------------------------------------------------------
    print(f"\n{'-' * 72}\n  AGAINST THE +/-{UNCERTAINTY_V:.0f} V UNCERTAINTY BUDGET\n{'-' * 72}")
    for amp, r in f.items():
        worst = np.abs(r["curve"].values - r["curve"].index.values).max() * 1000
        flag = "OUTSIDE" if worst > UNCERTAINTY_V else "within"
        print(f"  {amp}: worst absolute deviation {worst:6.2f} V  -> {flag} budget")

    print(f"\n{'-' * 72}")
    print("  WHAT THIS DATA CANNOT TELL YOU")
    print(f"{'-' * 72}")
    print("  The voltage monitor is inside the chain being measured. A gain of")
    print("  0.996 is consistent with an amplifier producing 0.4% low AND with")
    print("  a monitor reading 0.4% low. These produce identical CSVs. Resolving")
    print("  it requires an independent reference (HV probe) on the output.")
    print("  No correction factor should be applied on the strength of this file.\n")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python analyze_calibration.py <calibration.csv>")
    for p in sys.argv[1:]:
        report(p)