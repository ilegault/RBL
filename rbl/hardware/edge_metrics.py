"""
edge_metrics.py
Step-response metrics for the Dynamic Adjustment panel (Phase 8): overshoot,
settling time, flat-top creep, and rise time from an averaged voltage step;
peak current and current-tail duration from the corresponding current trace.

WHY THIS EXISTS
---------------
The DYNAMIC ADJ front-panel pot compensates the amplifier's feedback network
for load capacitance — structurally the same problem as scope-probe
compensation. Mistuned high, the loop sees the output arriving late,
overdrives, and overshoots and rings. Mistuned low, it sees the output
arriving early, backs off, and gives a slow corner with a settling tail.

Section 8.2 sets the bandwidth reality this module works inside: the
monitor BNCs are ~11 kHz (~30 us rise time), so the sub-10 us edge corner
and any ringing at the loop crossover are NOT measurable here — nothing in
this module tries to. What IS fully measurable is flat-top behaviour from
~100 us to hundreds of ms, which is exactly where compensation error shows
up as a slow exponential creep — `flat_top_creep_pct`'s sign is the steering
signal for which way to turn the pot.

SCOPE
-----
Pure math. No Qt, no hardware, no config lookups. Every function takes an
averaged step-response trace (already time-aligned and averaged across many
edges by the caller — rbl/services/dynamic_adjustment.py) and returns floats.
"""
import numpy as np


def overshoot_pct(v_trace, v_final: float) -> float:
    """(V_peak - V_final) / V_final * 100, sign-aware for a falling step.
    NaN if v_final is zero (the ratio is undefined)."""
    if v_final == 0 or len(v_trace) == 0:
        return float("nan")
    v_arr = np.asarray(v_trace, dtype=float)
    v_extreme = float(np.max(v_arr)) if v_final > 0 else float(np.min(v_arr))
    return (v_extreme - v_final) / v_final * 100.0


def settling_time_s(t, v_trace, v_final: float, tolerance_pct: float) -> float:
    """Time from t=0 until v_trace stays within tolerance_pct of v_final for
    the remainder of the trace. NaN if it never settles within the trace
    (a real "did not settle" is more useful than a confident wrong number)."""
    t = np.asarray(t, dtype=float)
    v_arr = np.asarray(v_trace, dtype=float)
    if v_final == 0 or t.size == 0:
        return float("nan")
    band = abs(v_final) * tolerance_pct / 100.0
    within = np.abs(v_arr - v_final) <= band
    outside_idx = np.flatnonzero(~within)
    if outside_idx.size == 0:
        return float(t[0])            # within tolerance for the whole trace
    last_outside = outside_idx[-1]
    if last_outside + 1 >= t.size:
        return float("nan")           # left the band and never came back
    return float(t[last_outside + 1])


def flat_top_creep_pct(t, v_trace, v_final: float, t_early_s: float = 10e-3,
                        t_late_s: float = 400e-3) -> float:
    """(V[t_late] - V[t_early]) / V_final * 100 — Section 8.4's creep metric.

    Positive: still rising after the initial transient (undercompensated,
    pot too low). Negative: drooping after the peak (overcompensated, pot
    too high). NaN if v_final is zero.
    """
    if v_final == 0:
        return float("nan")
    v_early = float(np.interp(t_early_s, t, v_trace))
    v_late = float(np.interp(t_late_s, t, v_trace))
    return (v_late - v_early) / v_final * 100.0


def rise_time_s(t, v_trace, v_final: float) -> float:
    """10-90% rise time. Monitor-bandwidth-limited (~30 us floor per Section
    8.2) but comparable across trials taken on the same monitor."""
    t = np.asarray(t, dtype=float)
    v_arr = np.asarray(v_trace, dtype=float)
    if v_final == 0 or t.size == 0:
        return float("nan")
    v10, v90 = 0.1 * v_final, 0.9 * v_final
    if v_final > 0:
        idx10 = np.flatnonzero(v_arr >= v10)
        idx90 = np.flatnonzero(v_arr >= v90)
    else:
        idx10 = np.flatnonzero(v_arr <= v10)
        idx90 = np.flatnonzero(v_arr <= v90)
    if idx10.size == 0 or idx90.size == 0:
        return float("nan")
    return float(t[idx90[0]] - t[idx10[0]])


def peak_current_ma(i_trace) -> float:
    """Worst-case current excursion magnitude on the edge."""
    if len(i_trace) == 0:
        return float("nan")
    return float(np.max(np.abs(np.asarray(i_trace, dtype=float))))


def current_tail_duration_s(t, i_trace, baseline_ma: float,
                             threshold_frac: float = 0.05) -> float:
    """Time from t=0 until the baseline-subtracted current first drops below
    threshold_frac of its peak excursion and stays there. A long tail means
    the amplifier is still settling — the current-domain analogue of
    settling_time_s. Returns 0.0 for a trace with no real excursion at all
    (peak == 0), NaN if it never settles."""
    t = np.asarray(t, dtype=float)
    i_arr = np.asarray(i_trace, dtype=float) - baseline_ma
    if t.size == 0 or i_arr.size == 0:
        return float("nan")
    peak = float(np.max(np.abs(i_arr)))
    if peak == 0:
        return 0.0
    threshold = threshold_frac * peak
    below = np.abs(i_arr) <= threshold
    outside_idx = np.flatnonzero(~below)
    if outside_idx.size == 0:
        return 0.0
    last_outside = outside_idx[-1]
    if last_outside + 1 >= t.size:
        return float("nan")
    return float(t[last_outside + 1])


def figure_of_merit(overshoot_pct_val: float, settling_1pct_s: float,
                     creep_pct_val: float) -> float:
    """A single sortable score, LOWER is better: |overshoot%| + |creep%| +
    settling time in ms. This is a documented convenience default, not a
    physical law — Section 8.3 asks the app to "declare a winner by figure
    of merit," and an operator can always re-rank the stored trials by any
    individual metric instead. NaN components are dropped rather than
    poisoning the whole score, since a trial that didn't settle within the
    trace should still be comparable on whatever it DID measure.
    """
    import math
    parts = [abs(x) for x in (overshoot_pct_val, creep_pct_val) if x == x]
    if settling_1pct_s == settling_1pct_s:
        parts.append(settling_1pct_s * 1000.0)
    return sum(parts) if parts else float("nan")


# ---------------------------------------------------------------------------
# Self-test — no hardware, no Qt.  Run:  python -m rbl.hardware.edge_metrics
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import math

    fs = 100_000.0
    t = np.arange(0, 0.5, 1.0 / fs)
    v_final = 500.0

    # A critically-damped-ish exponential rise with a touch of overshoot,
    # settling toward v_final — the "well compensated" case.
    tau = 5e-5
    v_over = v_final * (1.05 - 0.05 * np.exp(-t / tau)) * (1 - np.exp(-t / tau))
    over = overshoot_pct(v_over, v_final)
    assert over > 0, over
    print(f"[OK] overshoot detected: {over:.2f}%")

    # A pure first-order rise (no overshoot) should read ~0% overshoot.
    v_clean = v_final * (1 - np.exp(-t / tau))
    over_clean = overshoot_pct(v_clean, v_final)
    assert abs(over_clean) < 1.0, over_clean
    print(f"[OK] clean first-order rise has ~0 overshoot: {over_clean:.3f}%")

    settle = settling_time_s(t, v_clean, v_final, tolerance_pct=1.0)
    assert 0 < settle < 0.01, settle
    print(f"[OK] settling_time_s (1%) = {settle*1e6:.1f} us for tau={tau*1e6:.1f} us")

    never_settles = np.full_like(t, v_final * 1.5)   # stuck far from v_final
    assert math.isnan(settling_time_s(t, never_settles, v_final, 1.0))
    print("[OK] a trace that never settles reports NaN, not a wrong number")

    # Flat-top creep: undercompensated (still rising) vs overcompensated (drooping).
    v_undercomp = v_final * (1 - np.exp(-t / tau)) + 0.02 * v_final * (1 - np.exp(-t / 0.1))
    creep_under = flat_top_creep_pct(t, v_undercomp, v_final)
    assert creep_under > 0, creep_under
    v_overcomp = v_final * (1 - np.exp(-t / tau)) - 0.02 * v_final * (1 - np.exp(-t / 0.1))
    creep_over = flat_top_creep_pct(t, v_overcomp, v_final)
    assert creep_over < 0, creep_over
    print(f"[OK] creep sign distinguishes undercompensated (+{creep_under:.2f}%) "
          f"from overcompensated ({creep_over:.2f}%)")

    rise = rise_time_s(t, v_clean, v_final)
    assert rise > 0
    print(f"[OK] rise_time_s (10-90%) = {rise*1e6:.1f} us")

    # Current: an exponential pulse decaying to a baseline.
    i_trace = 20.0 * np.exp(-t / tau) + 0.5
    peak_i = peak_current_ma(i_trace)
    assert abs(peak_i - 20.5) < 0.1, peak_i
    tail = current_tail_duration_s(t, i_trace, baseline_ma=0.5)
    assert 0 < tail < 0.01, tail
    print(f"[OK] peak current {peak_i:.2f} mA, tail duration {tail*1e6:.1f} us")

    assert current_tail_duration_s(t, np.full_like(t, 0.5), baseline_ma=0.5) == 0.0
    print("[OK] a flat trace (no excursion) has zero tail duration")

    fom_good = figure_of_merit(0.5, 0.0001, 0.2)
    fom_bad = figure_of_merit(15.0, 0.05, 8.0)
    assert fom_good < fom_bad
    print(f"[OK] figure_of_merit ranks a clean trial ({fom_good:.3f}) "
          f"below a poor one ({fom_bad:.3f})")

    print("\n[OK] edge_metrics self-test passed")
