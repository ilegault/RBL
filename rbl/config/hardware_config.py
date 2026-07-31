"""
hardware_config.py
Central hardware mapping and calibration constants for the RBL beamline.

Covers:
  - Galil DMC-4103 slit motor axes (A,B,C,D) -> physical slits (X+, X-, Y+, Y-)
  - NEC log-amp LabJack channel assignments (AIN0-AIN3)
  - EEL5000.20.100 HV amplifier LabJack channel assignments (AIN6-AIN13)

Galil calibration constants from the 2HA075520 slit controller specification email:
  - Step mode = 1/2  (YA 2)
  - 200 steps/rev motor, 40:1 gear, 25.4 mm/rev lead-screw pitch
  - STEPS_PER_MM = 200 * 40 / 25.4 / 1  (full steps/mm) * 2  (half-step) = 629.92126
  - AC / DC = 25600 steps/s^2
  - SP = 1800 steps/s (normal), 900 steps/s (homing)
  - Motor type MT = -2.5, smoothing YB = 2.0, amplifier gain AG = 3
"""
from rbl.gui.theme import SLIT_COLORS

# Galil axis letter -> human-readable name
AXIS_NAMES = {
    "A": "X+",
    "B": "X-",
    "C": "Y+",
    "D": "Y-",
}

AXIS_LETTERS = list(AXIS_NAMES.keys())    # ["A", "B", "C", "D"]
AXIS_LABELS  = list(AXIS_NAMES.values())  # ["X+", "X-", "Y+", "Y-"]


# Steps per mm in 1/2-step mode (per spec email: 629.92126 half-steps/mm)
STEPS_PER_MM: dict[str, float] = {
    "A": 629.92126,
    "B": 629.92126,
    "C": 629.92126,
    "D": 629.92126,
}

# Zero offset in counts — "where is mechanical zero of the slit?"
ZERO_OFFSET_COUNTS: dict[str, int] = {
    "A": 0,
    "B": 0,
    "C": 0,
    "D": 0,
}

# Physical gap offset in mm per slit: the slits have a ~0.4 mm gap between them
# when both slits are at their homed/zeroed position, so each slit sits 0.2 mm
# from true centre. After the user zeros (DP=0), counts=0 displays as 0.2 mm.
MM_ZERO_OFFSET: dict[str, float] = {
    "A": 0.2,
    "B": 0.2,
    "C": 0.2,
    "D": 0.2,
}

# Full-scale span for the Overview tab's slit position bar charts and target
# entry, in absolute mm from beam centre. A DISPLAY convention only — the
# Galil's soft limits (BL/FL, read back on connect) remain the authority on how
# far a slit may actually travel, and a command outside them is refused by the
# controller, not by this number.
#
# 25 mm, not 10: the real travel is well past 10 mm, so a 10 mm full scale both
# misdrew every position (a slit two-thirds out looked pinned at the rail) and
# — because this same number is the Overview target spinbox's maximum — refused
# to accept a target the Stepper Motors tab would take without complaint. A
# display convention that silently caps what can be commanded is not a display
# convention.
SLIT_DISPLAY_MIN_MM = 0.0
SLIT_DISPLAY_MAX_MM = 25.0

# Increment the Overview target spinbox's own arrow keys apply (mm). The
# Overview is target-only — there are no separate step buttons — so this sizes
# nothing but the spinbox's own stepping.
SLIT_DEFAULT_STEP_MM = 0.1


# --- Motion parameters (per spec email) --------------------------------------
DEFAULT_SPEED_COUNTS_PER_SEC   = 1000
DEFAULT_ACCEL_COUNTS_PER_SEC2  = 25600
DEFAULT_JOG_SPEED              = 500   # cps

# --- Automatic homing: the seek phase ----------------------------------------
# The jog that puts an axis onto its home limit before HM runs — the "-Jog
# until it hits the limit" an operator does by hand before pressing Home.
#
# Fast, because it is a coarse approach and nothing about it sets the zero: the
# three HM passes that follow do that, and their slowest pass is 58 cps. Homing
# from the far end of the travel at 58 cps would take minutes and time the pass
# out; at 500 cps (0.79 mm/s) the whole travel is well under a minute.
HOME_SEEK_SPEED_COUNTS_PER_SEC = 500

# Ceiling on that jog. Generous — it has to cover the full travel from the
# forward limit, several times over, on the slowest axis. It is a fault
# detector ("the switch never tripped"), not a schedule.
HOME_SEEK_TIMEOUT_S = 180.0

# What HV is put back to once homing is done. HM's second stage — the slow
# re-approach that fixes where the zero lands — runs at HV, and the homing
# routine turns it down to 58 cps for the final pass. Restoring it matters for
# the same reason restoring SP does: the next home should start from a known
# value rather than inherit the last run's fine-approach speed.
DEFAULT_HOME_VELOCITY_COUNTS_PER_SEC = 256

# --- Amplifier / motor configuration (2HA075520 amplifier) -------------------
MOTOR_TYPE      = -2.5  # MT: step motor, active-high step pulse
STEP_RESOLUTION = 2     # YA: 1=full, 2=half, 4=quarter, 8=eighth
LOW_CURRENT_ON  = 1     # LC: reduced holding current (0=off, 1=on)
AMP_GAIN        = 3     # AG: amplifier gain setting
MOTOR_SMOOTHING = 2.0   # YB

# CN (switch config) — arguments per the CN reference. The ARGUMENT ORDER here
# is what the old comment got wrong (it listed the latch first and called arg2
# a limit polarity; arg2 is neither a polarity nor about the limits):
#
#   m = 1   LIMIT switch inputs active HIGH   (-1 = active low)
#   n = 1   HOME switch drives the motor FORWARD when the input is high
#           (-1 = reverse when high). A DIRECTION, not a polarity: it is what
#           HM and FE use to decide which way to search, which is why HM needs
#           no direction argument of its own.
#   o = -1  Latch input active low
#   p = 0   Inputs 5-8/13-16 are general-purpose, not selective-abort
#   q = 0   Abort input terminates program execution
CN_CONFIG = "1,1,-1,0,0"

# WHAT A TRIPPED SWITCH READS — and why CN alone does not tell you.
#
# It is tempting to read "CN m=1 -> limits active high" and conclude that a
# tripped limit reads 1. It does not, and the trap is the word ACTIVE. The
# _LF/_LR reference defines it electrically: "the condition when at least 1 mA
# of current is flowing through the input circuitry". That is a property of the
# CIRCUIT, not of whether the slit has reached its limit — and which way round
# those two sit depends on the switch WIRING, which no manual can tell you.
#
# These limits are NORMALLY CLOSED, so the circuit carries current while the
# axis is clear and reaching the limit BREAKS it. Composing that with m=1:
#
#     axis clear    -> switch closed -> current flows -> _LF/_LR read 1
#     limit tripped -> switch opens  -> no current    -> _LF/_LR read 0
#
# so a tripped limit reads LOW. NC is also the safe way round for exactly this
# reason: a cut cable stops the current and therefore reads as a tripped limit,
# not as a clear axis.
#
# Confirmed by three years of the bench reading "Idle" rather than "FWD LIMIT
# active" with the slits clear — which is the observation that caught an
# earlier attempt to derive these from CN's first argument alone.
LIMIT_SWITCH_TRIPPED_IS_LOW = True

# The home switch is a separate input with its own wiring, and CN's n argument
# is a search direction rather than a polarity, so nothing above determines
# this one. It was MEASURED on the controller instead:
#
#     MG _HMC  ->  0    with C sitting on its home switch
#     MG _HMB  ->  1    with B off its home switch
#
# Same reading as the limits, and the same NC logic behind it. Recorded here
# because it is the one value on this page that no manual can give you — the
# _HM operand's reference says only that it "contains the state of the home
# switch", and the state a switch is in when it is tripped is a fact about the
# wiring. Re-measure with those two commands if the slits are ever re-wired.
HOME_SWITCH_TRIPPED_IS_LOW = True


# --- Unit helpers -------------------------------------------------------------

def counts_to_mm(axis_letter: str, counts: float) -> float:
    """Step counts -> physical position in mm for the given axis.

    counts=0 (after user zeros) returns MM_ZERO_OFFSET (0.2 mm) because the
    slit sits 0.2 mm from true centre when homed.
    """
    sps    = STEPS_PER_MM[axis_letter]
    offset = ZERO_OFFSET_COUNTS[axis_letter]
    return (counts - offset) / sps + MM_ZERO_OFFSET[axis_letter]


def mm_to_counts(axis_letter: str, mm: float) -> int:
    """Physical position (mm) -> step counts for the given axis."""
    sps    = STEPS_PER_MM[axis_letter]
    offset = ZERO_OFFSET_COUNTS[axis_letter]
    return int(round((mm - MM_ZERO_OFFSET[axis_letter]) * sps + offset))


def cps_to_mm_per_sec(axis_letter: str, cps: float) -> float:
    """Counts-per-second -> mm/s."""
    return cps / STEPS_PER_MM[axis_letter]


def mm_per_sec_to_cps(axis_letter: str, mm_per_sec: float) -> int:
    """mm/s -> counts-per-second (rounded to int)."""
    return int(round(mm_per_sec * STEPS_PER_MM[axis_letter]))


# --- Log-amp (2HA032380, positive polarity, 0-6 V) ---------------------------
LOG_AMP_V_AT_1NA = 0.0   # V output at 1 nA input
LOG_AMP_V_AT_1MA = 6.0   # V output at 1 mA input

# The calibrated input span, in Amps: six decades, which is why anything
# plotting a log-amp current against a scale has to do it by decade.
LOG_AMP_MIN_A = 1e-9
LOG_AMP_MAX_A = 1e-3

# LabJack T7 analog input -> human-readable slit label
LABJACK_CHANNEL_MAP = {
    "AIN0": "X+",
    "AIN1": "X-",
    "AIN2": "Y+",
    "AIN3": "Y-",
}

# --- EEL5000.20.100 HV amplifier monitors ------------------------------------
# Each amplifier exposes two front-panel BNC monitors:
#   VOLTAGE MONITOR : 1000:1  -> 1 V at the BNC == 1 kV at the HV output
#   CURRENT MONITOR : 1 V     == 10 mA drawn from the amplifier
#
# Wired to the CB37 terminal board on AIN6..AIN13. AIN0..AIN3 are reserved for
# the log amps on the T7 body terminals and MUST NOT be duplicated on the CB37.
#
# Range must be +/-10 V on all eight: the current monitor reaches +/-10 V during
# the 100 mA / 4 ms transient the amplifier is rated for. A narrower range clips.

AMP_LABELS = ["X+", "X-", "Y+", "Y-"]

# amp label -> {"voltage": AIN name, "current": AIN name}
AMP_CHANNEL_MAP = {
    "X+": {"voltage": "AIN13", "current": "AIN12"},
    "X-": {"voltage": "AIN11", "current": "AIN10"},
    "Y+": {"voltage": "AIN9",  "current": "AIN8"},
    "Y-": {"voltage": "AIN7",  "current": "AIN6"},
}

# Flat, ordered list of every AIN the amplifier tab needs.
AMP_AIN_NAMES = [
    AMP_CHANNEL_MAP[lbl][kind]
    for lbl in AMP_LABELS
    for kind in ("voltage", "current")
]

# Reverse map: AIN name -> (amp label, kind) for the 8 amplifier monitors.
AIN_TO_AMP = {
    AMP_CHANNEL_MAP[amp][kind]: (amp, kind)
    for amp in AMP_LABELS
    for kind in ("voltage", "current")
}

# Scale factors (see EEL5000 manual, Specifications, p. 1-3)
VOLTAGE_MONITOR_KV_PER_VOLT = 1.0    # 1000:1 divider -> 1 V == 1 kV
CURRENT_MONITOR_MA_PER_VOLT = 10.0   # 1 V == 10 mA

# Display / sanity limits
AMP_MAX_KV     = 5.0    # amplifier rated +/-5 kV
AMP_MAX_MA_DC  = 20.0   # continuous DC rating
AMP_MAX_MA_PK  = 100.0  # 4 ms peak rating

# Plot colors, matched to the log-amp tab's palette for visual consistency.
# Single definition lives in rbl.gui.theme.SLIT_COLORS; re-exported here so
# existing SC.AMP_COLORS callers don't need to change.
AMP_COLORS = SLIT_COLORS

# The complete channel set the shared poll worker must read every cycle:
# 4 log amps + 8 amplifier monitors = 12 channels, ONE eReadNames round trip.
ALL_AIN_NAMES = list(LABJACK_CHANNEL_MAP.keys()) + AMP_AIN_NAMES


# --- Self-test ---------------------------------------------------------------

if __name__ == "__main__":
    assert AXIS_LETTERS == ["A", "B", "C", "D"]
    assert AXIS_LABELS  == ["X+", "X-", "Y+", "Y-"]

    # 1 mm = 629.92126 counts (round-trip), accounting for MM_ZERO_OFFSET
    for axis in AXIS_LETTERS:
        c = mm_to_counts(axis, 1.0)
        # 1.0 mm -> (1.0 - 0.2) * 629.92126 = 504 counts approx
        assert abs(c - round((1.0 - MM_ZERO_OFFSET[axis]) * STEPS_PER_MM[axis])) <= 1, \
            f"1 mm counts mismatch, got {c}"
        mm = counts_to_mm(axis, c)
        assert abs(mm - 1.0) < 0.002, f"Round-trip failed: {mm}"
    # counts=0 should display as MM_ZERO_OFFSET
    for axis in AXIS_LETTERS:
        assert abs(counts_to_mm(axis, 0) - MM_ZERO_OFFSET[axis]) < 0.001, \
            f"Zero offset check failed for {axis}"

    # mm/s ↔ cps
    for axis in AXIS_LETTERS:
        cps = mm_per_sec_to_cps(axis, 1.0)
        assert abs(cps - 630) < 1
        mms = cps_to_mm_per_sec(axis, cps)
        assert abs(mms - 1.0) < 0.002

    # Amplifier monitor map
    assert AMP_LABELS == ["X+", "X-", "Y+", "Y-"]
    assert len(AMP_AIN_NAMES) == 8
    assert AMP_AIN_NAMES == ["AIN13", "AIN12", "AIN11", "AIN10",
                             "AIN9", "AIN8", "AIN7", "AIN6"]
    # No overlap with the log amps — this is the whole safety point.
    assert not (set(AMP_AIN_NAMES) & set(LABJACK_CHANNEL_MAP.keys())), \
        "Amplifier AINs collide with log-amp AINs!"
    assert len(ALL_AIN_NAMES) == 12
    assert len(set(ALL_AIN_NAMES)) == 12, "Duplicate AIN in ALL_AIN_NAMES"
    for lbl in AMP_LABELS:
        assert lbl in AMP_COLORS

    print("[OK] hardware_config self-test passed")
    print(f"    STEPS_PER_MM = {STEPS_PER_MM['A']}")
    print(f"    1 mm = {mm_to_counts('A', 1.0)} counts")
    print(f"    1 mm/s = {mm_per_sec_to_cps('A', 1.0)} cps")
    print(f"    DEFAULT_SPEED = {DEFAULT_SPEED_COUNTS_PER_SEC} cps = "
          f"{cps_to_mm_per_sec('A', DEFAULT_SPEED_COUNTS_PER_SEC):.3f} mm/s")
