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
from enum import Enum
from pathlib import Path

from rbl.hardware.funcgen_driver import MAX_GEN_VOLTS

# --- Sweep ---------------------------------------------------------------

CAL_MAX_KV  = 4.0    # must equal-or-undercut MAX_GEN_VOLTS; asserted below
CAL_STEP_KV = 0.2     # -> 41 points across -4.0 .. +4.0 inclusive

CAL_SETTLE_S  = 0.5   # discarded after each setpoint change
CAL_COLLECT_S = 1.0   # averaged

CAL_PASSES = ("up", "down", "random")

# Combined amp + monitor + DAQ budget, in volts at the output. See module
# docstring. Deviations inside this band must not be reported as findings.
CAL_UNCERTAINTY_V = 30.0

# The all-8-amp-monitor-AIN stream profile this feature must run under (see
# Phase 0 recon: WAVEFORM and FULL both carry all 8 amp AINs; WAVEFORM is the
# higher per-channel rate of the two and doesn't need the log amps this
# feature has no use for).
CAL_PROFILE = "WAVEFORM"

# Repo-relative; there is no existing app-wide data/output directory to
# reuse (the only on-disk precedent, rbl/config/persistence.py, is a small
# JSON config store, not a run-output store).
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
    """The 41-point bipolar ladder from -CAL_MAX_KV to +CAL_MAX_KV, ascending."""
    n = round(2 * CAL_MAX_KV / CAL_STEP_KV) + 1
    return [round(-CAL_MAX_KV + i * CAL_STEP_KV, 10) for i in range(n)]


def sweep_points(pass_type: str, seed: int = None) -> list:
    """The commanded setpoints (kV) for one sweep pass, bracketed by zero.

    "up"     -> the ladder ascending, -CAL_MAX_KV .. +CAL_MAX_KV
    "down"   -> the ladder descending, +CAL_MAX_KV .. -CAL_MAX_KV
    "random" -> the same ladder, shuffled with a seeded RNG (reproducible;
                the seed is recorded in the metadata sidecar)

    Every pass is prefixed and suffixed with an explicit 0.0 point regardless
    of pass_type — the zero-drift tracker — even though the ladder itself
    already passes through zero in its interior.
    """
    ladder = _base_ladder()
    if pass_type == "up":
        seq = ladder
    elif pass_type == "down":
        seq = list(reversed(ladder))
    elif pass_type == "random":
        rng = random.Random(seed)
        seq = list(ladder)
        rng.shuffle(seq)
    else:
        raise ValueError(f"Unknown pass_type: {pass_type!r}")
    return [0.0] + seq + [0.0]


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

    assert len(up) == 43, f"expected 43 points (41 + bracketing zeros), got {len(up)}"
    assert up[0] == 0.0 and up[-1] == 0.0
    inner_up = up[1:-1]
    assert inner_up == sorted(inner_up), "up pass is not ascending"
    print(f"[OK] sweep_points('up') is ascending, length {len(up)} (41 + leading/trailing zero)")

    assert len(down) == 43
    assert down[0] == 0.0 and down[-1] == 0.0
    inner_down = down[1:-1]
    assert inner_down == list(reversed(inner_up)), \
        "down pass's ladder is not the reverse of up's"
    print("[OK] sweep_points('down') == reversed(up) modulo the bracketing zeros")

    r1 = sweep_points("random", seed=42)
    r2 = sweep_points("random", seed=42)
    assert r1 == r2, "same seed must reproduce the same order"
    assert len(r1) == 43
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
