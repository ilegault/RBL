"""
calibration_config.py
Constants and small pure helpers for the HV amplifier calibration feature.

WHAT THIS FEATURE DOES AND DOES NOT DO
---------------------------------------
This sweeps each deflection channel through a bipolar DC ladder and records
what the EEL5000 VOLTAGE MONITOR reports back via the LabJack T7, alongside
what was commanded. The voltage monitor is itself a link in the measurement
chain (command -> Rigol output -> amplifier gain -> monitor divider -> T7
ADC), so this data CANNOT distinguish "the amplifier under-produces" from
"the monitor under-reads" — both produce a byte-identical CSV.

This module therefore holds no correction-factor constant and no "apply"
path. It logs and displays; it never corrects. See docs/calibration.md.

UNCERTAINTY BUDGET
-------------------
From the EEL5000 manual: amplifier accuracy 0.5% FS (+/-25 V at +/-5 kV FS),
voltage monitor accuracy 0.1% FS (+/-5 V), plus LabJack T7 error on the
+/-10 V range and <500 mVrms output noise (0.5 mV at the monitor). Combined,
legitimate disagreement between commanded and measured is roughly +/-30 V.
Deviations inside CAL_UNCERTAINTY_V are noise, not findings.
"""
import random
import sys
from enum import Enum
from pathlib import Path

from rbl.hardware.funcgen_driver import MAX_GEN_VOLTS

# --- Sweep ---------------------------------------------------------------

CAL_MAX_KV  = 5.0    # must equal-or-undercut MAX_GEN_VOLTS; asserted below
CAL_STEP_KV = 0.2     # -> 51 points across -5.0 .. +5.0 inclusive

CAL_SETTLE_S  = 1.5   # discarded after each setpoint change (amplifier + monitor settle)
CAL_COLLECT_S = 1.0   # averaged

CAL_PASSES = ("up", "down", "random")

# --- AC sweep -------------------------------------------------------------
# AC mode commands a sine wave at CAL_AC_FREQ_HZ and sweeps the amplitude
# from 0 to CAL_MAX_KV in CAL_STEP_KV increments.  The setpoints are
# peak output kV (= Vpp/2 * AMP_GAIN / 1000).
CAL_AC_FREQ_HZ   = 1000.0   # 1 kHz — well within the EEL5000 bandwidth
CAL_AC_SETTLE_S  = 2.0      # AC needs more settle time (waveform stabilization)
CAL_AC_COLLECT_S = 2.0      # average over more cycles for a stable RMS reading

# Combined amp + monitor + DAQ budget, in volts at the output. See module
# docstring. Deviations inside this band must not be reported as findings.
CAL_UNCERTAINTY_V = 30.0

# The all-8-amp-monitor-AIN stream profile this feature must run under (see
# Phase 0 recon: WAVEFORM and FULL both carry all 8 amp AINs; WAVEFORM is the
# higher per-channel rate of the two and doesn't need the log amps this
# feature has no use for).
CAL_PROFILE = "WAVEFORM"

# In a PyInstaller one-folder build, sit beside the executable so the data
# folder is at a predictable, user-visible location (dist/RBL/data/calibration).
# In development, use the repo root (two packages up from this file).
if getattr(sys, "frozen", False):
    CAL_OUTPUT_DIR = Path(sys.executable).resolve().parent / "data" / "calibration"
else:
    CAL_OUTPUT_DIR = Path(__file__).resolve().parents[2] / "data" / "calibration"

# --- Drift -----------------------------------------------------------------

DRIFT_DEFAULT_KV       = 3.0
DRIFT_LOG_INTERVAL_S   = 1.0
DRIFT_MAX_UNATTENDED_H = 12.0   # only when load condition is DISCONNECTED
DRIFT_MAX_ATTENDED_H   = 2.0    # hard cap when load condition is ON_PLATES


class LoadCondition(Enum):
    """What is physically connected to the amplifier's HV output.

    The app cannot verify this in software — see the pre-run checklist
    dialog in calibration_tab.py — but it gates the drift-duration guard
    (rbl/services/calibration_runner.py) and is recorded in every CSV's
    metadata sidecar.
    """
    DISCONNECTED = "DISCONNECTED"
    ON_PLATES    = "ON_PLATES"


# --- Sweep point generation -------------------------------------------------

def _base_ladder() -> list:
    """The bipolar ladder from -CAL_MAX_KV to +CAL_MAX_KV, ascending."""
    n = round(2 * CAL_MAX_KV / CAL_STEP_KV) + 1
    return [round(-CAL_MAX_KV + i * CAL_STEP_KV, 10) for i in range(n)]


def _positive_half() -> list:
    """0.0, step, 2*step, ..., CAL_MAX_KV (ascending, excluding 0)."""
    n = round(CAL_MAX_KV / CAL_STEP_KV)
    return [round((i + 1) * CAL_STEP_KV, 10) for i in range(n)]


def sweep_points(pass_type: str, seed: int = None) -> list:
    """The commanded setpoints (kV) for one sweep pass, starting from zero.

    All passes start at 0 and ramp outward so the amplifier never sees a
    large voltage step from rest.  The largest step between consecutive
    points is CAL_STEP_KV (except "random", which shuffles freely but still
    starts and ends at 0).

    "up"     -> 0 .. +CAL_MAX_KV, back through 0, then 0 .. -CAL_MAX_KV, 0
                (positive ascending, then negative descending)
    "down"   -> 0 .. -CAL_MAX_KV, back through 0, then 0 .. +CAL_MAX_KV, 0
                (negative descending first, then positive ascending)
    "random" -> the full ladder shuffled with a seeded RNG (reproducible;
                the seed is recorded in the metadata sidecar)

    Every pass is prefixed and suffixed with an explicit 0.0 point —
    the zero-drift tracker.
    """
    if pass_type == "up":
        pos = _positive_half()          # 0.2, 0.4, ..., 5.0
        neg = [-v for v in pos]         # -0.2, -0.4, ..., -5.0
        seq = pos + list(reversed(pos)) + [0.0] + neg + list(reversed(neg))
    elif pass_type == "down":
        pos = _positive_half()
        neg = [-v for v in pos]
        seq = neg + list(reversed(neg)) + [0.0] + pos + list(reversed(pos))
    elif pass_type == "random":
        rng = random.Random(seed)
        seq = list(_base_ladder())
        rng.shuffle(seq)
    else:
        raise ValueError(f"Unknown pass_type: {pass_type!r}")
    return [0.0] + seq + [0.0]


def ac_sweep_points() -> list:
    """Amplitude setpoints (peak kV) for the AC sweep: 0 → CAL_MAX_KV → 0.

    The sweep ramps amplitude up from 0 to CAL_MAX_KV in CAL_STEP_KV steps,
    then back down to 0 — one complete up-down cycle.  Every value is a
    non-negative peak output kV (the sine swings ±this around zero offset).
    """
    pos = _positive_half()   # 0.2, 0.4, ..., 5.0
    return [0.0] + pos + list(reversed(pos)) + [0.0]


# --- Self-check at import: fail loudly, not silently over the safety cap ---

assert CAL_MAX_KV <= MAX_GEN_VOLTS, (
    f"CAL_MAX_KV={CAL_MAX_KV} exceeds MAX_GEN_VOLTS={MAX_GEN_VOLTS} — the "
    "function generator's own safety cap. If MAX_GEN_VOLTS was lowered, "
    "CAL_MAX_KV must come down with it, not silently command over it."
)


# --- Self-test ---------------------------------------------------------------

if __name__ == "__main__":
    up = sweep_points("up")
    down = sweep_points("down")

    assert up[0] == 0.0 and up[-1] == 0.0
    assert down[0] == 0.0 and down[-1] == 0.0
    print(f"[OK] sweep_points('up') length {len(up)}, starts and ends at 0.0")
    print(f"[OK] sweep_points('down') length {len(down)}, starts and ends at 0.0")

    # Up pass: first non-zero value should be positive (ramps positive first)
    first_nonzero = next(v for v in up if v != 0.0)
    assert first_nonzero > 0, f"up pass should start positive, got {first_nonzero}"
    print(f"[OK] up pass starts positive ({first_nonzero})")

    # Down pass: first non-zero value should be negative (ramps negative first)
    first_nonzero = next(v for v in down if v != 0.0)
    assert first_nonzero < 0, f"down pass should start negative, got {first_nonzero}"
    print(f"[OK] down pass starts negative ({first_nonzero})")

    # Max step between consecutive points in up/down (excluding random)
    for name, pts in [("up", up), ("down", down)]:
        max_step = max(abs(pts[i+1] - pts[i]) for i in range(len(pts)-1))
        assert max_step <= CAL_STEP_KV + 1e-9, \
            f"{name}: max step {max_step} exceeds {CAL_STEP_KV}"
        print(f"[OK] {name} max consecutive step: {max_step:.3f} kV")

    # Both passes cover the full range
    for name, pts in [("up", up), ("down", down)]:
        assert max(pts) >= CAL_MAX_KV - 1e-9, f"{name} doesn't reach +{CAL_MAX_KV}"
        assert min(pts) <= -CAL_MAX_KV + 1e-9, f"{name} doesn't reach -{CAL_MAX_KV}"
    print("[OK] both passes cover full +/- range")

    r1 = sweep_points("random", seed=42)
    r2 = sweep_points("random", seed=42)
    assert r1 == r2, "same seed must reproduce the same order"
    print("[OK] sweep_points('random', seed=42) is deterministic across two calls")

    for pass_type in CAL_PASSES:
        pts = sweep_points(pass_type, seed=1)
        assert pts[0] == 0.0 and pts[-1] == 0.0, f"{pass_type} pass must bracket 0.0"
    print("[OK] every pass starts and ends at 0.0")

    for pass_type in CAL_PASSES:
        pts = sweep_points(pass_type, seed=1)
        assert all(abs(p) <= CAL_MAX_KV + 1e-12 for p in pts), \
            f"{pass_type} pass exceeds CAL_MAX_KV"
    print("[OK] no point exceeds CAL_MAX_KV in magnitude")

    assert CAL_MAX_KV <= MAX_GEN_VOLTS
    print(f"[OK] CAL_MAX_KV ({CAL_MAX_KV}) <= MAX_GEN_VOLTS ({MAX_GEN_VOLTS})")

    print("[OK] calibration_config self-test passed")
