"""
load_model.py
Turns lock-in fundamentals and charge integrals into a load's complex
admittance, and turns the operating envelope of Section 1.6 of
docs/AMP_ENVELOPE_AND_HV_SAFETY_PLAN.md into curves a plot can draw.

WHY THIS EXISTS
---------------
`CAL_LOAD_CAP_PF` in `calibration_config.py` is a single global number,
hand-derived once from a one-off analysis of two AC sweeps. It answers "how
big is the load" but not "is this channel leaky" or "did the model just
break down at 3 kHz" — and it cannot, by construction, tell the four
channels apart. This module is the arithmetic that a *measurement* needs:
given a lock-in fundamental (already computed by `ac_metrics.py` — this
module does not touch a waveform, only the numbers `ac_metrics` already
extracted from one) or a charge integral, recover capacitance AND leakage
conductance, per channel, per condition.

A pure capacitor has current leading voltage by exactly 90 degrees. Real
admittance is what is left over: leakage current, corona, or dielectric
loss in the feedthrough or plates. That residue is exactly the diagnostic
that tells a healthy channel from the leaky one behind the historical Y-axis
faults (see Appendix A of the plan) — without touching an amplifier.

SCOPE
-----
Pure math. No Qt, no hardware, no config lookups. Everything takes plain
floats/arrays and returns plain floats/dicts.

A NOTE ON THE SHAPE CONSTANT
-----------------------------
`envelope_walls` needs the same sine/triangle/square k-factor that
`calibration_config.ac_shape_k()` already defines. This module keeps its own
copy (`_SHAPE_K`) instead of importing `calibration_config`, to preserve the
"no config lookups" property pure-math modules in this repo are required to
have (see docs/AMP_ENVELOPE_AND_HV_SAFETY_PLAN.md Section 0). The two tables
are cross-checked against each other in `tests/test_load_model.py` so they
cannot silently drift apart.
"""
import math

import numpy as np

# k factors for I_pk = k * f * C * V_pk. Must match
# `calibration_config._AC_PEAK_CURRENT_K` exactly — see the note above.
_SHAPE_K = {
    "sine": 2 * math.pi,
    "triangle": 4.0,
    "ramp": 4.0,
    "square": 2 * math.pi,   # conservative: true limit is amplifier slew, not load
}

# EEL5000.20.100 manual, p. 1-3: slew rate > 300 V/us. Measured (Section 1.5
# of the plan) never to be reached into this load, but it is still a real
# ceiling the envelope must show, and it belongs here rather than as a
# function argument because it is a fixed amplifier spec, not a per-run
# measurement.
AMP_SLEW_V_PER_US = 300.0


def _shape_k(shape: str) -> float:
    try:
        return _SHAPE_K[shape.lower()]
    except (KeyError, AttributeError):
        raise ValueError(f"Unknown drive shape {shape!r}; expected one of {sorted(_SHAPE_K)}")


def admittance_from_fundamentals(i_fund_ma: float, v_fund_kv: float,
                                  phase_deg: float, freq_hz: float) -> dict:
    """Complex load admittance from lock-in fundamentals.

    Returns {"c_pf": float, "g_us": float, "loss_tangent": float}.

    `phase_deg` is the current's phase relative to the voltage's, in the
    convention of `ac_metrics.phase_difference_deg(phase_current, phase_voltage)`
    — i.e. positive means current LEADS voltage, which is what a capacitor
    does. Treating the load as a parallel (G, C) pair:

        Y = I / V = |Y| * exp(j*phase)  =  G + j*omega*C

        C  = |Y| * sin(phase) / (2*pi*f)
        G  = |Y| * cos(phase)
        tan(delta) = G / (2*pi*f*C)

    A pure capacitor has phase = 90 deg exactly, so G = 0 and tan(delta) = 0.
    Any real part is conductance (leakage, corona, dielectric loss) and is
    the diagnostic that distinguishes a healthy channel from a leaky one.
    """
    nan = float("nan")
    if (freq_hz <= 0 or v_fund_kv <= 0
            or not math.isfinite(i_fund_ma) or not math.isfinite(v_fund_kv)
            or not math.isfinite(phase_deg)):
        return {"c_pf": nan, "g_us": nan, "loss_tangent": nan}

    y_siemens = (i_fund_ma * 1e-3) / (v_fund_kv * 1e3)
    phase_rad = math.radians(phase_deg)
    omega = 2.0 * math.pi * freq_hz

    c_farad = y_siemens * math.sin(phase_rad) / omega
    g_siemens = y_siemens * math.cos(phase_rad)
    loss_tangent = (g_siemens / (omega * c_farad)) if c_farad != 0 else nan

    return {
        "c_pf": c_farad * 1e12,
        "g_us": g_siemens * 1e6,
        "loss_tangent": loss_tangent,
    }


def capacitance_from_charge(current_ma, dt_s: float, baseline_ma: float,
                             delta_v_kv: float) -> float:
    """C in pF from the charge integral across one step edge.

        integral(I dt) = Q = C * dV

    A low-pass filter has unity DC gain, so the 11 kHz monitor pole spreads
    the current pulse in time but preserves its AREA exactly. This
    measurement is therefore immune to the monitor's bandwidth — unlike any
    peak-based reading. Baseline must be subtracted before integrating or the
    DC offset (the amplifier's steady leakage/bias current) dominates the
    result, since it is integrated over the whole window along with the
    transient.

    `current_ma` is a uniformly-sampled window straddling the edge, in mA;
    `dt_s` is the sample spacing; `baseline_ma` is the pre-edge steady-state
    current to subtract; `delta_v_kv` is the commanded step size (signed or
    unsigned — only its magnitude is used, since capacitance is a positive
    physical quantity regardless of which way the step went).
    """
    x = np.asarray(current_ma, dtype=float) - baseline_ma
    if x.size < 2 or delta_v_kv == 0:
        return float("nan")
    q_coulombs = np.trapezoid(x * 1e-3, dx=dt_s)
    delta_v_v = abs(delta_v_kv) * 1000.0
    return abs(q_coulombs / delta_v_v) * 1e12


def envelope_walls(load_pf: float, trip_ma: float, shape: str,
                    max_kv: float, max_f_hz: float, n_points: int = 200) -> dict:
    """The four walls of Section 1.6 as f(V) curves, for plotting.

    Returns a dict of same-length arrays over `freq_hz`, plus the scalar
    bandwidth wall:

        current_wall_kv   V_pk at which the current limit binds, per frequency
        voltage_wall_kv   flat line at max_kv
        slew_wall_kv      V_pk at which the amplifier's slew rate binds
        bandwidth_wall_hz max_f_hz (a vertical line, not a function of V)
        envelope_kv       elementwise min of the three V-based walls, NaN
                          past the bandwidth wall — the actually-usable region

    The current wall dominates everywhere that matters for this rig (Section
    1.6); the slew wall is included because it is one of the four walls, even
    though Section 1.5 shows it is never reached into this load.
    """
    if load_pf <= 0 or trip_ma <= 0 or max_kv <= 0 or max_f_hz <= 0:
        raise ValueError("envelope_walls requires positive load_pf, trip_ma, max_kv, max_f_hz")

    k = _shape_k(shape)
    c_farad = load_pf * 1e-12

    freq_hz = np.geomspace(max_f_hz / 1000.0, max_f_hz * 1.2, n_points)
    current_wall_kv = (trip_ma * 1e-3) / (k * c_farad * freq_hz) / 1000.0
    voltage_wall_kv = np.full_like(freq_hz, max_kv)
    slew_wall_kv = (AMP_SLEW_V_PER_US * 1e6) / (k * freq_hz) / 1000.0

    envelope_kv = np.minimum(np.minimum(current_wall_kv, voltage_wall_kv), slew_wall_kv)
    envelope_kv = np.where(freq_hz <= max_f_hz, envelope_kv, np.nan)

    return {
        "freq_hz": freq_hz,
        "current_wall_kv": current_wall_kv,
        "voltage_wall_kv": voltage_wall_kv,
        "slew_wall_kv": slew_wall_kv,
        "bandwidth_wall_hz": float(max_f_hz),
        "envelope_kv": envelope_kv,
        "shape_k": k,
    }


# ---------------------------------------------------------------------------
# Self-test — no hardware, no Qt.  Run:  python -m rbl.hardware.load_model
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # A pure 1200 pF capacitor at 1 kHz, driven with a 1 V(pk) source, current
    # leading by exactly 90 deg: I_pk = 2*pi*f*C*V = 2*pi*1000*1200e-12*1000 (V
    # in volts) = 7.54 mA.
    f = 1000.0
    c_true_pf = 1200.0
    v_pk_kv = 1.0
    i_pk_ma = 2 * math.pi * f * (c_true_pf * 1e-12) * (v_pk_kv * 1000.0) * 1e3
    r = admittance_from_fundamentals(i_pk_ma, v_pk_kv, 90.0, f)
    assert abs(r["c_pf"] - c_true_pf) < 1e-6, r
    assert abs(r["g_us"]) < 1e-9, r
    assert abs(r["loss_tangent"]) < 1e-9, r
    print(f"[OK] pure capacitor: C={r['c_pf']:.1f} pF, G={r['g_us']:.4f} uS, "
          f"tan(delta)={r['loss_tangent']:.4f}")

    # A leaky channel: 10 deg off from 90 introduces a real (conductance) part.
    r_leaky = admittance_from_fundamentals(i_pk_ma, v_pk_kv, 80.0, f)
    assert r_leaky["g_us"] > 0, "leakage should show up as positive conductance"
    assert r_leaky["loss_tangent"] > 0
    print(f"[OK] leaky channel (10 deg off): G={r_leaky['g_us']:.3f} uS, "
          f"tan(delta)={r_leaky['loss_tangent']:.4f}")

    # Degenerate inputs must not raise.
    for bad in (admittance_from_fundamentals(1.0, 0.0, 90.0, 1000.0),
                admittance_from_fundamentals(1.0, 1.0, 90.0, 0.0),
                admittance_from_fundamentals(float("nan"), 1.0, 90.0, 1000.0)):
        assert math.isnan(bad["c_pf"])
    print("[OK] degenerate admittance inputs handled")

    # Charge integral: a 100 us square current pulse of 10 mA into a 2 kV step
    # carries Q = 1e-3 s... use a simple rectangular pulse for an exact check.
    fs = 1_000_000.0
    dt = 1.0 / fs
    n = 1000
    baseline = 0.5
    pulse_ma = np.full(n, baseline)
    pulse_ma[100:200] = baseline + 10.0    # 10 mA above baseline for 100 us
    delta_v_kv = 2.0
    q_expected_c = 10e-3 * 100e-6          # 10 mA * 100 us
    c_expected_pf = q_expected_c / (delta_v_kv * 1000.0) * 1e12
    c_pf = capacitance_from_charge(pulse_ma, dt, baseline, delta_v_kv)
    assert abs(c_pf - c_expected_pf) / c_expected_pf < 0.02, (c_pf, c_expected_pf)
    print(f"[OK] charge integral: C={c_pf:.2f} pF (expected {c_expected_pf:.2f} pF)")
    assert math.isnan(capacitance_from_charge([1.0], dt, 0.0, 1.0))
    assert math.isnan(capacitance_from_charge([1.0, 2.0], dt, 0.0, 0.0))
    print("[OK] degenerate charge-integral inputs handled")

    # Envelope walls reproduce the Section 1.6 table: 1200 pF, triangle,
    # 20 mA trip -> current wall at 5 kV should read ~833 Hz-equivalent, i.e.
    # at f=833.33 Hz the current wall should read back ~5 kV.
    walls = envelope_walls(load_pf=1200.0, trip_ma=20.0, shape="triangle",
                            max_kv=5.0, max_f_hz=10_000.0)
    f_probe = 833.33
    v_at_f = np.interp(f_probe, walls["freq_hz"], walls["current_wall_kv"])
    assert abs(v_at_f - 5.0) < 0.02, v_at_f
    print(f"[OK] envelope current wall at {f_probe} Hz = {v_at_f:.3f} kV (expect 5.0)")

    table = [(5.0, 833.0), (4.0, 1042.0), (2.0, 2083.0), (1.0, 4167.0)]
    for v_kv, f_hz in table:
        v_check = np.interp(f_hz, walls["freq_hz"], walls["current_wall_kv"])
        assert abs(v_check - v_kv) / v_kv < 0.01, (v_kv, f_hz, v_check)
    print("[OK] envelope_walls reproduces the full Section 1.6 table (20 mA)")

    try:
        envelope_walls(0.0, 20.0, "triangle", 5.0, 10_000.0)
        raise AssertionError("expected ValueError on non-positive load_pf")
    except ValueError:
        print("[OK] envelope_walls rejects non-positive inputs")

    print("\n[OK] load_model self-test passed")
