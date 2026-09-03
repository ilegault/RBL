"""
regulation.py
Discriminates a healthy amplifier from one that has stopped following its
own input — the software substitute for the fault-monitor BNCs the user has
declined to wire.

WHY THIS EXISTS
---------------
The user runs the EEL5000 permanently in LIMIT mode with the current pot at
maximum. In LIMIT mode the amplifier clamps current and stops following its
input rather than shutting down — the commanded voltage and the actual plate
voltage silently diverge. Any calibration point taken during such an event is
invalid and nothing currently detects it. The amplifier can also shut itself
off entirely (it has, once), and the app will happily keep commanding a dead
channel unless something is watching both monitors.

Discriminating what went wrong needs BOTH monitors, not just one: a low
voltage reading alone is ambiguous between "amplifier is off" and "amplifier
is alive but current-limited". `classify` is that discrimination.

SCOPE
-----
Pure math. No Qt, no hardware. Use the lock-in fundamental from
`ac_metrics.py` for AC and the window mean for DC to get `measured`/`commanded`
here — this module does not touch a waveform itself, reuse `ac_metrics`,
do not reimplement it.
"""

# Below this fraction of the amplifier's rated peak (kV), the ratio between
# measured and commanded voltage is dominated by noise and offsets rather
# than by anything meaningful about regulation — classification is skipped
# entirely (state "idle") rather than producing a noisy verdict near zero.
DEFAULT_ARM_THRESHOLD_KV = 0.1

# A commanded voltage this close to zero makes the ratio itself meaningless
# (division by ~0), independent of the arm threshold above.
_ZERO_COMMAND_EPS_KV = 1e-6


def regulation_ratio(measured: float, commanded: float) -> float:
    """measured / commanded. ~1.0 is healthy.

    Returns NaN when `commanded` is too close to zero for the ratio to mean
    anything — callers must treat that as "idle", never as a fault, since the
    ratio is mathematically undefined there rather than merely unhealthy.
    """
    if abs(commanded) <= _ZERO_COMMAND_EPS_KV:
        return float("nan")
    return measured / commanded


def classify(v_ratio: float, i_measured_ma: float, i_limit_ma: float,
             commanded_kv: float, arm_threshold_kv: float = DEFAULT_ARM_THRESHOLD_KV,
             current_limited_ratio: float = 0.5,
             current_at_limit_frac: float = 0.9) -> tuple:
    """-> (state, reason)

    Discriminating what went wrong needs BOTH monitors:

      V ~ 0 and I ~ 0                  -> "amp_off"
          amplifier tripped, HV enable open, or unplugged
      V low but nonzero, I at limit    -> "current_limited"
          still on; output is not following input; data is invalid
      V correct, I normal              -> "ok"
      commanded ~ 0                    -> "idle"  (ratio is undefined; do not
                                                    divide by it)

    `current_at_limit_frac` is the fraction of `i_limit_ma` above which
    current is considered "at the limit" (some margin below 100% because a
    current-limited amplifier's monitor reading is itself noisy near the
    clamp). `current_limited_ratio` is the v_ratio ceiling below which the
    amplifier is considered to be failing to follow its input, distinct from
    the near-zero threshold that means "off" — both are compared against
    `abs(i_measured_ma)` and `abs(v_ratio)` so the sign of AC swings never
    flips the classification.
    """
    if abs(commanded_kv) < arm_threshold_kv:
        return "idle", f"commanded {commanded_kv:.4g} kV is below the {arm_threshold_kv:.4g} kV arm threshold"

    if v_ratio != v_ratio:  # NaN check without importing math for one use
        return "idle", "commanded voltage too close to zero for a ratio"

    at_limit = abs(i_measured_ma) >= current_at_limit_frac * i_limit_ma

    if abs(v_ratio) < 0.05 and not at_limit:
        return "amp_off", (
            f"measured voltage is ~0 ({v_ratio * 100:.1f}% of commanded) and current "
            f"{i_measured_ma:.3g} mA is well below the {i_limit_ma:.3g} mA limit — "
            "amplifier appears off, tripped, or disconnected"
        )

    if abs(v_ratio) < current_limited_ratio and at_limit:
        return "current_limited", (
            f"measured voltage is {v_ratio * 100:.1f}% of commanded while current "
            f"{i_measured_ma:.3g} mA is at the {i_limit_ma:.3g} mA limit — "
            "output is not following input, this data point is invalid"
        )

    if abs(v_ratio) < current_limited_ratio:
        return "amp_off", (
            f"measured voltage is {v_ratio * 100:.1f}% of commanded but current is not "
            "at the limit — amplifier is not producing the commanded output"
        )

    return "ok", f"measured voltage is {v_ratio * 100:.1f}% of commanded, current within limit"


# ---------------------------------------------------------------------------
# Self-test — no hardware, no Qt.  Run:  python -m rbl.hardware.regulation
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import math

    assert math.isnan(regulation_ratio(1.0, 0.0))
    assert abs(regulation_ratio(2.0, 4.0) - 0.5) < 1e-12
    print("[OK] regulation_ratio")

    # Idle: commanded near zero, never a fault regardless of the ratio's value.
    state, _ = classify(v_ratio=float("nan"), i_measured_ma=0.0, i_limit_ma=20.0,
                         commanded_kv=0.0)
    assert state == "idle"
    print("[OK] idle when commanded ~ 0")

    # Healthy: ratio ~1, current well under the limit.
    state, _ = classify(v_ratio=0.98, i_measured_ma=4.0, i_limit_ma=20.0, commanded_kv=2.0)
    assert state == "ok", state
    print("[OK] ok classification")

    # amp_off: both monitors read ~zero.
    state, _ = classify(v_ratio=0.01, i_measured_ma=0.1, i_limit_ma=20.0, commanded_kv=3.0)
    assert state == "amp_off", state
    print("[OK] amp_off when both monitors are ~0")

    # current_limited: voltage low, current pinned at the limit.
    state, reason = classify(v_ratio=0.2, i_measured_ma=19.5, i_limit_ma=20.0, commanded_kv=5.0)
    assert state == "current_limited", state
    assert "invalid" in reason
    print("[OK] current_limited when V is low but I is at the limit")

    # Negative (AC swing) values must classify the same as their magnitude.
    state, _ = classify(v_ratio=-0.98, i_measured_ma=-4.0, i_limit_ma=20.0, commanded_kv=-2.0)
    assert state == "ok", state
    print("[OK] sign-independent classification")

    print("\n[OK] regulation self-test passed")
