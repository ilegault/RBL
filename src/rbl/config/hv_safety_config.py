"""
hv_safety_config.py
Tunables for the vacuum <-> HV interlock (rbl/hardware/hv_interlock.py):
the pressure ladder, the absolute lockout, and the gauge staleness timeout.

Split out from `hv_interlock.py` itself (rather than living as bare module
constants there) so the interlock's pure math stays independent of where its
numbers come from, matching the split between `calibration_config.py` and
the pure-math functions in `calibration_config.py` that consume it — the
numbers are policy, the arithmetic is not.

PROVENANCE — placeholders pending measurement
-----------------------------------------------
`HV_PRESSURE_LIMITS` and `HV_PRESSURE_ABSOLUTE_LOCKOUT_TORR` are the
plan-stage placeholders from docs/AMP_ENVELOPE_AND_HV_SAFETY_PLAN.md Section
3.2, not a measured discharge onset. Phase 1 Mode B (DC leakage vs. voltage,
`rbl/services/load_characterizer.py`) measures the real onset per channel; a
knee in that curve is the channel's actual DC limit. Once that data exists,
replace the ladder below with the measured values and update this comment
with the measurement date, in the style of
`calibration_config.CAL_LOAD_CAP_PF`'s provenance comment.
"""

# (max_kv_permitted, pressure_torr_required_below), decreasing kv order.
HV_PRESSURE_LIMITS = [
    (5.0, 1e-5),
    (3.0, 5e-5),
    (1.0, 1e-4),
]

# No HV at all at or above this pressure.
HV_PRESSURE_ABSOLUTE_LOCKOUT_TORR = 1e-3

# If neither gauge (VGC083, XGS600) has reported within this many seconds,
# pressure is treated as UNKNOWN rather than as "whatever it last read" — see
# `hv_interlock.interlock_status(..., pressure_known=False)`. Generous enough
# to tolerate the gauges' own poll cadence and normal serial jitter, short
# enough that a genuinely dead gauge is caught within a few missed polls.
GAUGE_STALE_TIMEOUT_S = 5.0


if __name__ == "__main__":
    assert HV_PRESSURE_LIMITS == sorted(HV_PRESSURE_LIMITS, key=lambda t: -t[0])
    assert all(kv <= 5.0 for kv, _ in HV_PRESSURE_LIMITS)
    assert GAUGE_STALE_TIMEOUT_S > 0
    print("[OK] hv_safety_config self-test passed")
