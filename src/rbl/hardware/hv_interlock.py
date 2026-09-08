"""
hv_interlock.py
Vacuum-pressure policy for HV: how much plate voltage is permitted at a given
chamber pressure, and what to do about a commanded voltage that exceeds it.

WHY THIS EXISTS
---------------
The user's only two genuine amplifier faults — an out-of-regulation event on
Y- and a hard self-shutdown on Y+ — both happened during held 0->5 kV DC
steps. Section 1.5 of docs/AMP_ENVELOPE_AND_HV_SAFETY_PLAN.md shows the step
current itself cannot explain a shutdown; the likely mechanism is discharge
in the chamber, which held DC gives time to develop and AC scanning does not.
This module is the policy layer that keeps HV away from pressures where that
discharge is likely, in software, since the fault-monitor BNCs are not wired.

The thresholds below are PLACEHOLDERS pending Phase 1 Mode B (DC leakage vs.
voltage), which measures the real discharge onset per channel. Replace them
once that data exists, and record the new provenance here in the same style
as `calibration_config.CAL_LOAD_CAP_PF`'s comment.

SCOPE
-----
Pure math + policy. No Qt, no hardware. Callers own reading the gauge and
acting on the returned state. The ladder and lockout values themselves live
in `rbl.config.hv_safety_config` (imported below) so their provenance
comments stay with the numbers, not the arithmetic — the same split as
`funcgen_safety.py` importing `MAX_GEN_VOLTS` from `funcgen_driver`.
"""
from rbl.config.hv_safety_config import (
    HV_PRESSURE_ABSOLUTE_LOCKOUT_TORR,
    HV_PRESSURE_LIMITS,
)


def max_permitted_kv(pressure_torr: float) -> float:
    """Highest plate voltage allowed at this pressure, or 0.0 if locked out.

    `pressure_torr` must already be a resolved number — callers that cannot
    get one (gauge stale or disconnected) must not call this with a guess;
    see `interlock_status`'s `pressure_known` parameter, which is the
    supported way to express "no reading".
    """
    if pressure_torr >= HV_PRESSURE_ABSOLUTE_LOCKOUT_TORR:
        return 0.0
    best = 0.0
    for kv, required_below in HV_PRESSURE_LIMITS:
        if pressure_torr < required_below and kv > best:
            best = kv
    return best


def interlock_status(pressure_torr: float, commanded_kv: float,
                      pressure_known: bool = True) -> tuple:
    """-> ("ok"|"warn"|"block", human-readable reason)

    `pressure_known=False` models a stale or disconnected gauge: HV increases
    must be blocked in that state because "no data" is not "good vacuum" —
    the absence of a reading is never treated as evidence of anything.
    "warn" is returned only when there IS a valid ceiling below the absolute
    lockout but the commanded voltage exceeds it while pressure is still
    comfortably clear of the lockout threshold; anything at or above the
    absolute lockout is always "block", never merely "warn".
    """
    if not pressure_known:
        return "block", ("vacuum reading is stale or unavailable — "
                          "HV blocked until pressure is confirmed")

    if pressure_torr >= HV_PRESSURE_ABSOLUTE_LOCKOUT_TORR:
        return "block", (
            f"pressure {pressure_torr:.2e} torr is at or above the absolute HV "
            f"lockout of {HV_PRESSURE_ABSOLUTE_LOCKOUT_TORR:.1e} torr"
        )

    ceiling = max_permitted_kv(pressure_torr)
    if commanded_kv <= ceiling + 1e-9:
        return "ok", (f"{commanded_kv:.3g} kV permitted at {pressure_torr:.2e} torr "
                       f"(ceiling {ceiling:.3g} kV)")

    return "warn", (
        f"commanded {commanded_kv:.3g} kV exceeds the {ceiling:.3g} kV ceiling "
        f"permitted at {pressure_torr:.2e} torr"
    )


# ---------------------------------------------------------------------------
# Self-test — no hardware, no Qt.  Run:  python -m rbl.hardware.hv_interlock
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    assert max_permitted_kv(1e-6) == 5.0
    assert max_permitted_kv(2e-5) == 3.0
    assert max_permitted_kv(7e-5) == 1.0
    assert max_permitted_kv(5e-4) == 0.0
    assert max_permitted_kv(1e-3) == 0.0
    print("[OK] max_permitted_kv ladder")

    status, reason = interlock_status(1e-6, 5.0)
    assert status == "ok", reason
    status, reason = interlock_status(2e-5, 5.0)
    assert status == "warn", reason
    status, reason = interlock_status(1e-3, 0.5)
    assert status == "block", reason
    status, reason = interlock_status(1e-6, 5.0, pressure_known=False)
    assert status == "block", reason
    print("[OK] interlock_status ok/warn/block")

    # Absolute lockout always blocks, never merely warns, even at 0 kV commanded.
    status, _ = interlock_status(2e-3, 0.0)
    assert status == "block"
    print("[OK] absolute lockout blocks unconditionally")

    print("\n[OK] hv_interlock self-test passed")
