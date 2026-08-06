#!/usr/bin/env python3
"""
Analyze an HV amplifier calibration CSV produced by the RBL Calibration tab.

Usage:
    python analyze_calibration.py cal_20260804T111951.csv
    python analyze_calibration.py cal_*.csv --outdir plots
    python analyze_calibration.py cal_20260804T111951.csv --no-plots
    python analyze_calibration.py cal_20260804T111951.csv --show

Writes a text report to stdout and a set of PNGs alongside the CSV
(or into --outdir). Handles both sweep-mode and drift-mode files.

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

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")          # switched to an interactive backend by --show
import matplotlib.pyplot as plt

# Combined amp (0.5% FS) + monitor (0.1% FS) + DAQ uncertainty, volts at output.
# Deviations inside this band are noise, not findings.
UNCERTAINTY_V = 30.0

# EEL5000 output offset spec, volts DC.
AMP_OFFSET_SPEC_V = 2.0

# Fixed colour per channel so every figure agrees with every other figure.
CH_COLORS = {"X+": "#c1440e", "X-": "#e08214",
             "Y+": "#1f5673", "Y-": "#4a9db5"}
CH_ORDER = ["X+", "X-", "Y+", "Y-"]

PASS_STYLE = {"up": "-", "down": "--", "random": ":"}


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


# =========================================================================
#  PLOTS
# =========================================================================
#
# Why these particular plots:
#
#   1. deviation      The money plot. Measured minus commanded, in volts,
#                     against the +/-30 V budget band. Plotting measured vs
#                     commanded directly is useless -- a 0.4% error on a
#                     y=x line is invisible to the eye. The residual is not.
#   2. nonlinearity   Deviation from each channel's OWN best-fit line. This
#                     separates "wrong scale factor" (a tilt, harmless and
#                     correctable) from "wrong shape" (curvature, which is a
#                     real amplifier problem).
#   3. noise          std at each setpoint. Corona onset shows as a hockey
#                     stick at high |V|.
#   4. hysteresis     Up-sweep minus down-sweep. Non-zero means the output
#                     depends on where it came from, not just where it is.
#   5. crosstalk      Heatmap. Driving one plate should not move another.
#   6. current        Load current vs voltage. The diagnostic for leakage
#                     once you have both load conditions to subtract.
#   7. zero drift     The 0 kV revisits against elapsed time. Thermal drift
#                     during the run masquerades as nonlinearity if ignored.
# =========================================================================


def _style(ax, xlabel, ylabel, title):
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_title(title, fontsize=10)
    ax.grid(alpha=0.25, linewidth=0.6)
    ax.axhline(0, color="0.4", linewidth=0.8)


# Set by --show so figures survive to plt.show() instead of being closed.
_KEEP_OPEN = False


def _save(fig, outdir, stem, name):
    path = Path(outdir) / f"{stem}_{name}.png"
    fig.savefig(path, dpi=140, bbox_inches="tight")
    if not _KEEP_OPEN:
        plt.close(fig)
    print(f"    wrote {path}")
    return path


def plot_deviation(driven_v, outdir, stem):
    """Measured minus commanded, in volts, with the uncertainty band."""
    fig, ax = plt.subplots(figsize=(9, 5.5))
    ax.axhspan(-UNCERTAINTY_V, UNCERTAINTY_V, color="0.85", zorder=0,
               label=f"+/-{UNCERTAINTY_V:.0f} V combined uncertainty")
    for amp in [a for a in CH_ORDER if a in set(driven_v.amp_label)]:
        g = driven_v[driven_v.amp_label == amp]
        m = g.groupby("commanded_kv").converted_value.mean()
        dev = (m.values - m.index.values) * 1000
        ax.plot(m.index.values, dev, "o-", ms=3.5, linewidth=1.4,
                color=CH_COLORS.get(amp), label=amp)
    _style(ax, "commanded (kV)", "measured - commanded (V)",
           "Deviation from ideal\n"
           "A straight tilted line is a scale-factor error; curvature is a real nonlinearity")
    ax.legend(fontsize=8, ncol=3)
    return _save(fig, outdir, stem, "deviation")


def plot_nonlinearity(driven_v, f, outdir, stem):
    """Residual from each channel's own best-fit line."""
    fig, axes = plt.subplots(2, 2, figsize=(11, 7), sharex=True)
    for ax, amp in zip(axes.ravel(), CH_ORDER):
        if amp not in f:
            ax.set_visible(False)
            continue
        g = driven_v[driven_v.amp_label == amp]
        for ptype, sub in g.groupby("pass_type"):
            m = sub.groupby("commanded_kv").converted_value.mean()
            resid = (m.values - (f[amp]["gain"] * m.index.values
                                 + f[amp]["offset_v"] / 1000)) * 1000
            ax.plot(m.index.values, resid, PASS_STYLE.get(ptype, "-"),
                    marker="o", ms=2.5, linewidth=1.1,
                    color=CH_COLORS.get(amp), alpha=0.8, label=ptype)
        _style(ax, "commanded (kV)", "residual (V)",
               f"{amp}  gain {f[amp]['gain']:.5f}  "
               f"offset {f[amp]['offset_v']:+.1f} V")
        ax.legend(fontsize=7)
    fig.suptitle("Nonlinearity: deviation from each channel's own fit",
                 fontsize=11)
    fig.tight_layout()
    return _save(fig, outdir, stem, "nonlinearity")


def plot_noise(driven_v, outdir, stem):
    """std at each setpoint -- the corona / partial-discharge check."""
    fig, ax = plt.subplots(figsize=(9, 5))
    for amp in [a for a in CH_ORDER if a in set(driven_v.amp_label)]:
        g = driven_v[driven_v.amp_label == amp]
        m = g.groupby("commanded_kv").std_v.mean() * 1000
        ax.plot(m.index.values, m.values, "o-", ms=3, linewidth=1.2,
                color=CH_COLORS.get(amp), label=amp)
    _style(ax, "commanded (kV)", "std at monitor (mV)",
           "Noise vs voltage\n"
           "Flat is healthy. A rise at high |V| is discharge onset.")
    ax.legend(fontsize=8, ncol=4)
    return _save(fig, outdir, stem, "noise")


def plot_hysteresis(driven_v, outdir, stem):
    """Up-sweep minus down-sweep at matched setpoints."""
    if not {"up", "down"} <= set(driven_v.pass_type.unique()):
        return None
    fig, ax = plt.subplots(figsize=(9, 5))
    for amp in [a for a in CH_ORDER if a in set(driven_v.amp_label)]:
        g = driven_v[driven_v.amp_label == amp]
        p = g.pivot_table(index="commanded_kv", columns="pass_type",
                          values="converted_value")
        h = (p["up"] - p["down"]) * 1000
        ax.plot(h.index.values, h.values, "o-", ms=3, linewidth=1.2,
                color=CH_COLORS.get(amp), label=amp)
    _style(ax, "commanded (kV)", "up - down (V)",
           "Hysteresis\n"
           "Non-zero means the output depends on approach direction")
    ax.legend(fontsize=8, ncol=4)
    return _save(fig, outdir, stem, "hysteresis")


def plot_crosstalk(cross_v, outdir, stem):
    """Heatmap of volts induced on each victim per kV on each driver."""
    if cross_v.empty:
        return None
    rows = []
    for (drv, vic), g in cross_v.groupby(["driven_amp", "amp_label"]):
        m = g.groupby("commanded_kv").converted_value.mean()
        rows.append((drv, vic, np.polyfit(m.index.values, m.values, 1)[0] * 1000))
    ct = (pd.DataFrame(rows, columns=["driven", "victim", "v"])
            .pivot(index="driven", columns="victim", values="v")
            .reindex(index=CH_ORDER, columns=CH_ORDER))

    fig, ax = plt.subplots(figsize=(6.2, 5.2))
    data = ct.values.astype(float)
    im = ax.imshow(np.abs(data), cmap="magma_r", vmin=0)
    ax.set_xticks(range(len(CH_ORDER)), CH_ORDER)
    ax.set_yticks(range(len(CH_ORDER)), CH_ORDER)
    ax.set_xlabel("victim channel")
    ax.set_ylabel("driven channel")
    ax.set_title("Crosstalk: volts induced per kV driven\n"
                 "(diagonal is blank -- a channel cannot cross-talk to itself)",
                 fontsize=10)
    for i in range(len(CH_ORDER)):
        for j in range(len(CH_ORDER)):
            val = data[i, j]
            if np.isnan(val):
                ax.text(j, i, "--", ha="center", va="center", color="0.5")
            else:
                ax.text(j, i, f"{val:+.3f}", ha="center", va="center",
                        fontsize=8,
                        color="white" if abs(val) > np.nanmax(np.abs(data)) * 0.6
                        else "black")
    fig.colorbar(im, ax=ax, label="|V per kV|", shrink=0.8)
    return _save(fig, outdir, stem, "crosstalk")


def plot_current(driven_i, outdir, stem):
    """Load current vs commanded voltage."""
    fig, ax = plt.subplots(figsize=(9, 5))
    for amp in [a for a in CH_ORDER if a in set(driven_i.amp_label)]:
        g = driven_i[driven_i.amp_label == amp]
        m = g.groupby("commanded_kv").converted_value.mean()
        ax.plot(m.index.values, m.values, "o-", ms=3, linewidth=1.2,
                color=CH_COLORS.get(amp), label=amp)
    _style(ax, "commanded (kV)", "monitor current (mA)",
           "Current monitor\n"
           "Within 1% of FS accuracy this may be monitor artifact. "
           "The real signal is the difference between load conditions.")
    ax.legend(fontsize=8, ncol=4)
    return _save(fig, outdir, stem, "current")


def plot_zero_drift(driven_v, outdir, stem):
    """The 0 kV revisits, against elapsed time."""
    z = driven_v[driven_v.commanded_kv == 0.0]
    if z.empty:
        return None
    fig, ax = plt.subplots(figsize=(9, 5))
    for amp in [a for a in CH_ORDER if a in set(z.amp_label)]:
        g = z[z.amp_label == amp].sort_values("t_elapsed_s")
        ax.plot(g.t_elapsed_s / 60, g.converted_value * 1000, "o-",
                ms=4, linewidth=1.2, color=CH_COLORS.get(amp), label=amp)
    _style(ax, "elapsed time (min)", "measured at 0 kV commanded (V)",
           "Zero-point stability across the run\n"
           "Slope here is thermal drift, which would otherwise fake nonlinearity")
    ax.legend(fontsize=8, ncol=4)
    return _save(fig, outdir, stem, "zero_drift")


def plot_drift_run(df, outdir, stem):
    """Time series for a drift-mode file: voltage and current vs hours."""
    dr = df[df.pass_type == "drift"]
    if dr.empty:
        return None
    dv = dr[(dr.kind == "voltage") & (dr.driven_amp == dr.amp_label)]
    di = dr[(dr.kind == "current") & (dr.driven_amp == dr.amp_label)]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 7), sharex=True)
    for amp in [a for a in CH_ORDER if a in set(dv.amp_label)]:
        g = dv[dv.amp_label == amp].sort_values("t_elapsed_s")
        hrs = g.t_elapsed_s / 3600
        base = g.converted_value.iloc[0]
        ax1.plot(hrs, (g.converted_value - base) * 1000, linewidth=1.0,
                 color=CH_COLORS.get(amp), label=amp)
        # Least-squares drift rate in volts per hour.
        if len(hrs) > 2:
            slope = np.polyfit(hrs, g.converted_value * 1000, 1)[0]
            print(f"    drift rate {amp}: {slope:+.3f} V/hour")
    _style(ax1, "", "drift from first sample (V)",
           "Drift run: output deviation from its starting value")
    ax1.legend(fontsize=8, ncol=4)

    for amp in [a for a in CH_ORDER if a in set(di.amp_label)]:
        g = di[di.amp_label == amp].sort_values("t_elapsed_s")
        ax2.plot(g.t_elapsed_s / 3600, g.converted_value, linewidth=1.0,
                 color=CH_COLORS.get(amp), label=amp)
    _style(ax2, "elapsed time (hours)", "monitor current (mA)",
           "Current draw over the same window\n"
           "A slow rise at fixed voltage is the early signature of insulation leakage")
    ax2.legend(fontsize=8, ncol=4)
    fig.tight_layout()
    return _save(fig, outdir, stem, "drift")


def make_plots(df, driven_v, driven_i, cross_v, f, outdir, stem):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    print(f"\n{'-' * 72}\n  PLOTS\n{'-' * 72}")

    if "drift" in set(df.pass_type.unique()):
        plot_drift_run(df, outdir, stem)

    sweep = driven_v[driven_v.pass_type != "drift"]
    if not sweep.empty:
        plot_deviation(sweep, outdir, stem)
        plot_nonlinearity(sweep, f, outdir, stem)
        plot_noise(sweep, outdir, stem)
        plot_hysteresis(sweep, outdir, stem)
        plot_zero_drift(sweep, outdir, stem)
    if not cross_v.empty:
        plot_crosstalk(cross_v[cross_v.pass_type != "drift"], outdir, stem)
    if not driven_i.empty:
        plot_current(driven_i[driven_i.pass_type != "drift"], outdir, stem)


def report(path, outdir=None, do_plots=True):
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

    if do_plots:
        p = Path(path)
        make_plots(df, driven_v, driven_i, cross_v, f,
                   outdir or p.parent, p.stem)
        print()


def main():
    ap = argparse.ArgumentParser(
        description="Analyze RBL HV amplifier calibration CSVs.")
    ap.add_argument("csv", nargs="+", help="one or more calibration CSV files")
    ap.add_argument("--outdir", default=None,
                    help="where to write PNGs (default: next to the CSV)")
    ap.add_argument("--no-plots", action="store_true",
                    help="text report only")
    ap.add_argument("--show", action="store_true",
                    help="open the figures interactively instead of only saving")
    args = ap.parse_args()

    if args.show:
        global _KEEP_OPEN
        _KEEP_OPEN = True
        try:
            matplotlib.use("TkAgg", force=True)
        except Exception as e:
            print(f"[warn] could not switch to an interactive backend: {e}")

    for path in args.csv:
        report(path, outdir=args.outdir, do_plots=not args.no_plots)

    if args.show:
        plt.show()


if __name__ == "__main__":
    main()