"""
amp_test_config.py
Constants for the ten-group EEL5000 amplifier test matrix.

SCOPE
-----
This feature CAPTURES. It does not analyse. Every number produced here is
written to disk in a documented, self-describing layout; Allan deviation,
Welch PSD, capacitance back-solve, Bode magnitude and settling-time
extraction all live in processing/ and read these files after the fact.

Nothing in this module applies a correction to anything.
"""
import math
import sys
from pathlib import Path

from rbl.hardware.funcgen_driver import MAX_GEN_VOLTS
from rbl.config.hardware_config import AMP_MAX_KV, AMP_MAX_MA_DC

# ---------------------------------------------------------------------------
# Output layout
# ---------------------------------------------------------------------------

# In a PyInstaller one-folder build, sit beside the executable so the data
# folder is at a predictable, user-visible location (dist/RBL/data/amp_tests).
# In development, use the repo root (two packages up from this file).
if getattr(sys, "frozen", False):
    AMT_OUTPUT_DIR = Path(sys.executable).resolve().parent / "data" / "amp_tests"
else:
    AMT_OUTPUT_DIR = Path(__file__).resolve().parents[2] / "data" / "amp_tests"

AMT_RAW_SUBDIR      = "raw"
AMT_SUMMARY_CSV     = "summary.csv"
AMT_METADATA_JSON   = "metadata.json"

# Shared across all runs — lives at the top of AMT_OUTPUT_DIR, NOT inside
# a run folder. Written automatically by G1.1 (trip_ma per amp) and G2.1
# (load_cap_pf per amp) as those tests complete. Consumed at run time by the
# trip interlock and the envelope guard.
AMT_MEASURED_LIMITS_JSON = "measured_limits.json"

# ---------------------------------------------------------------------------
# Raw-capture size guards
# ---------------------------------------------------------------------------
# SINGLE_FAST produces 100 000 samples/s/channel. A careless 3600 s spec
# would try to write ~1.4 GB. These constants are the hard stops.

# T7 16-bit ADC on ±10 V has far less than 24 bits of real information;
# float32 is lossless with respect to the instrument and halves the file size.
RAW_DTYPE = "float32"

# 200 s at SINGLE_FAST (100 kS/s) = 20 M samples ≈ 80 MB per capture.
RAW_MAX_SAMPLES_PER_CAPTURE = 20_000_000

# Hard ceiling for one complete run (all levels, all target AINs).
RAW_MAX_BYTES_PER_RUN = 8 * 1024 ** 3   # 8 GiB

# When True the runner computes the projected byte total from the spec BEFORE
# commanding anything and refuses the run if it exceeds RAW_MAX_BYTES_PER_RUN.
RAW_ESTIMATE_REFUSE = True

# ---------------------------------------------------------------------------
# Profile-switch handshake
# ---------------------------------------------------------------------------
# Switching profiles is a full eStreamStop→reconfigure→eStreamStart cycle.
# Payloads arriving during the restart carry the old stride / old channel list
# and must be discarded — see AmpTestRunner._enter_await_profile for why this
# matters and what de-interleave mismatch looks like in practice.

# Abort and emit an error if a correctly-configured payload never arrives
# within this many seconds of requesting the switch.
PROFILE_SWITCH_TIMEOUT_S = 5.0

# After the first correctly-configured payload arrives, discard this many more
# windows to cover the stream restart transient before entering SETTLE.
PROFILE_SETTLE_WINDOWS = 3

# ---------------------------------------------------------------------------
# Trip / fault interlock
# ---------------------------------------------------------------------------

# A measured current above trip_set_ma * TRIP_MARGIN on a test that does NOT
# expect a trip is a fault, not data.
TRIP_MARGIN = 1.15

# Measured |kV| below this fraction of a commanded |kV| >= 0.5 kV means the
# output has collapsed.
COLLAPSE_RATIO = 0.20

# Consecutive windows required to declare a collapse, so a single settling
# transient is not mistaken for a trip.
COLLAPSE_WINDOWS = 5

# Where the driven channel is commanded the instant a trip is detected.
TRIP_BACKOFF_KV = 0.0

# ---------------------------------------------------------------------------
# Operating-envelope guard
# ---------------------------------------------------------------------------
# Implements the "Operating envelope on plates" table from the
# Findings & Limits sheet, in code.

# A GUESS until G3 measures it. The runner reads the measured value from
# front-panel/run state when one exists and falls back to this with a warning.
LOAD_CAP_PF_DEFAULT = 130.0

# Re-imported from hardware_config. Asserted below to catch drift.
# AMP_MAX_KV  — already imported at the top of this module
# AMP_MAX_MA_DC — already imported at the top of this module

# Front-panel CURRENT ADJUSTMENT range.
POT_RANGE_MA = (0.5, 10.0)


def peak_current_ma(freq_hz: float, peak_kv: float, load_pf: float) -> float:
    """I_pk = 2*pi*f*C*V_pk, in mA.  Capacitive load only."""
    # C [F] = load_pf * 1e-12
    # V_pk [V] = peak_kv * 1000
    # I_pk [A] = 2*pi*f * C * V_pk
    # I_pk [mA] = 2*pi*f * (load_pf*1e-12) * (peak_kv*1000) * 1000
    #           = 2*pi*f * load_pf * peak_kv * 1e-6
    return 2.0 * math.pi * freq_hz * load_pf * peak_kv * 1e-6


def envelope_ceiling_kv(freq_hz: float, trip_set_ma: float,
                        load_pf: float) -> tuple[float, str]:
    """(max commandable peak kV, what binds it).

    Returns the lower of the amplifier's 5 kV rating and the ceiling imposed
    by the front-panel current pot.  The second element is one of
    "amp 5 kV limit" | "CURRENT POT" | "20 mA rating", matching the
    Findings & Limits sheet's 'What binds' column so the two can be compared
    directly.
    """
    # Invert peak_current_ma to find the peak kV at which I_pk equals a given
    # current limit:  peak_kv = I_ma / (2*pi*f * load_pf * 1e-6)
    denominator = 2.0 * math.pi * freq_hz * load_pf * 1e-6

    # Ceiling from the front-panel current pot setting.
    pot_ceil_kv = trip_set_ma / denominator

    # Ceiling from the amplifier's continuous DC rating.
    rating_ceil_kv = AMP_MAX_MA_DC / denominator

    # Current-based ceiling is whichever is more restrictive.
    if pot_ceil_kv <= rating_ceil_kv:
        current_ceil_kv = pot_ceil_kv
        current_what = "CURRENT POT"
    else:
        current_ceil_kv = rating_ceil_kv
        current_what = "20 mA rating"

    # Final ceiling is the lower of current-based and amplifier kV rating.
    if AMP_MAX_KV <= current_ceil_kv:
        return AMP_MAX_KV, "amp 5 kV limit"
    else:
        return current_ceil_kv, current_what

# ---------------------------------------------------------------------------
# Endurance duration policy (G9)
# ---------------------------------------------------------------------------
# Isaac's stated progression: 2 h on plates now, 12 h on plates later.
#
# RAISING THIS CONSTANT:
#   Raising ENDURANCE_ON_PLATES_MAX_H above calibration_config.DRIFT_MAX_ATTENDED_H
#   also arms the extra acknowledgement in the G9 checklist (Phase 10).
#   That is intentional — the checklist knows to ask once this crosses the
#   attended cap. Change only this constant; the runner and checklist adapt.
ENDURANCE_ON_PLATES_MAX_H = 2.0

# How often a short gain probe is interleaved during a G9.1 hold.
ENDURANCE_PROBE_INTERVAL_S = 1200.0   # 20 min, per matrix row 35

# How often the running statistics are logged during a G9.1 hold.
ENDURANCE_LOG_INTERVAL_S = 1.0

# ---------------------------------------------------------------------------
# Import-time assertions — fail loudly, do not silently ship a bad config
# ---------------------------------------------------------------------------

assert AMP_MAX_KV <= MAX_GEN_VOLTS, (
    f"AMP_MAX_KV={AMP_MAX_KV} must not exceed MAX_GEN_VOLTS={MAX_GEN_VOLTS}"
)
assert POT_RANGE_MA[0] < POT_RANGE_MA[1] <= AMP_MAX_MA_DC, (
    f"POT_RANGE_MA={POT_RANGE_MA} invalid or exceeds AMP_MAX_MA_DC={AMP_MAX_MA_DC}"
)
assert RAW_MAX_SAMPLES_PER_CAPTURE * 4 < RAW_MAX_BYTES_PER_RUN, (
    f"RAW_MAX_SAMPLES_PER_CAPTURE={RAW_MAX_SAMPLES_PER_CAPTURE} * 4 bytes "
    f"must be under RAW_MAX_BYTES_PER_RUN={RAW_MAX_BYTES_PER_RUN}"
)
assert ENDURANCE_ON_PLATES_MAX_H >= 0.0

# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== amp_test_config self-test ===")

    # peak_current_ma checks (values from the Findings & Limits sheet)
    v = peak_current_ma(1000, 5.0, 130)
    assert abs(v - 4.084) < 0.001, f"peak_current_ma(1000,5,130)={v:.4f}, expected ~4.084"
    print(f"[OK] peak_current_ma(1000, 5.0, 130) = {v:.4f} mA  (expected ~4.084)")

    v = peak_current_ma(2000, 5.0, 130)
    assert abs(v - 8.168) < 0.001, f"peak_current_ma(2000,5,130)={v:.4f}, expected ~8.168"
    print(f"[OK] peak_current_ma(2000, 5.0, 130) = {v:.4f} mA  (expected ~8.168)")

    v = peak_current_ma(10000, 5.0, 130)
    assert abs(v - 40.841) < 0.001, f"peak_current_ma(10000,5,130)={v:.4f}, expected ~40.841"
    print(f"[OK] peak_current_ma(10000, 5.0, 130) = {v:.4f} mA  (expected ~40.841)")

    # envelope_ceiling_kv checks
    kv, what = envelope_ceiling_kv(2000, 10.0, 130)
    assert abs(kv - 5.0) < 1e-6 and what == "amp 5 kV limit", \
        f"envelope_ceiling_kv(2000,10,130)=({kv:.4f},{what!r}), expected (5.0,'amp 5 kV limit')"
    print(f"[OK] envelope_ceiling_kv(2000, 10.0, 130) = ({kv:.4f}, {what!r})")

    kv, what = envelope_ceiling_kv(3000, 10.0, 130)
    assert abs(kv - 4.0809) < 0.001 and what == "CURRENT POT", \
        f"envelope_ceiling_kv(3000,10,130)=({kv:.4f},{what!r}), expected (~4.0809,'CURRENT POT')"
    print(f"[OK] envelope_ceiling_kv(3000, 10.0, 130) = ({kv:.4f}, {what!r})")

    kv, what = envelope_ceiling_kv(5000, 10.0, 130)
    assert abs(kv - 2.4485) < 0.001 and what == "CURRENT POT", \
        f"envelope_ceiling_kv(5000,10,130)=({kv:.4f},{what!r}), expected (~2.4485,'CURRENT POT')"
    print(f"[OK] envelope_ceiling_kv(5000, 10.0, 130) = ({kv:.4f}, {what!r})")

    kv, what = envelope_ceiling_kv(10000, 10.0, 130)
    assert abs(kv - 1.2243) < 0.001 and what == "CURRENT POT", \
        f"envelope_ceiling_kv(10000,10,130)=({kv:.4f},{what!r}), expected (~1.2243,'CURRENT POT')"
    print(f"[OK] envelope_ceiling_kv(10000, 10.0, 130) = ({kv:.4f}, {what!r})")

    kv, what = envelope_ceiling_kv(2000, 0.5, 130)
    assert abs(kv - 0.306) < 0.001 and what == "CURRENT POT", \
        f"envelope_ceiling_kv(2000,0.5,130)=({kv:.4f},{what!r}), expected (~0.306,'CURRENT POT')"
    print(f"[OK] envelope_ceiling_kv(2000, 0.5, 130) = ({kv:.4f}, {what!r})  "
          f"<-- low pot: operating point not reachable at 2 kHz")

    kv, what = envelope_ceiling_kv(100, 0.5, 130)
    assert abs(kv - 5.0) < 1e-6 and what == "amp 5 kV limit", \
        f"envelope_ceiling_kv(100,0.5,130)=({kv:.4f},{what!r}), expected (5.0,'amp 5 kV limit')"
    print(f"[OK] envelope_ceiling_kv(100, 0.5, 130) = ({kv:.4f}, {what!r})  "
          f"<-- at 100 Hz even min pot allows >5 kV; amp rating binds")

    # Import-time assert sanity
    assert AMP_MAX_KV <= MAX_GEN_VOLTS
    assert POT_RANGE_MA[0] < POT_RANGE_MA[1] <= AMP_MAX_MA_DC
    assert RAW_MAX_SAMPLES_PER_CAPTURE * 4 < RAW_MAX_BYTES_PER_RUN
    assert ENDURANCE_ON_PLATES_MAX_H >= 0.0
    print("[OK] all import-time assertions hold")

    print("[OK] amp_test_config self-test passed")
