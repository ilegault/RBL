"""
slit_config.py
Hardware mapping and calibration for the 4-jaw beam-line slits.

Galil DMC-4103 axis letters (A,B,C,D) -> physical slit jaws (X+, X-, Y+, Y-).

Calibration constants from the 2HA075520 slit controller specification email:
  - Step mode = 1/2  (YA 2)
  - 200 steps/rev motor, 40:1 gear, 25.4 mm/rev lead-screw pitch
  - STEPS_PER_MM = 200 * 40 / 25.4 / 1  (full steps/mm) * 2  (half-step) = 629.92126
  - AC / DC = 25600 steps/s^2
  - SP = 1800 steps/s (normal), 900 steps/s (homing)
  - Motor type MT = -2.5, smoothing YB = 2.0, amplifier gain AG = 3
"""

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

# Zero offset in counts — "where is mechanical zero of the jaw?"
ZERO_OFFSET_COUNTS: dict[str, int] = {
    "A": 0,
    "B": 0,
    "C": 0,
    "D": 0,
}

# --- Motion parameters (per spec email) --------------------------------------
DEFAULT_SPEED_COUNTS_PER_SEC   = 1800
DEFAULT_ACCEL_COUNTS_PER_SEC2  = 25600
DEFAULT_JOG_SPEED              = 1800    # cps
HOMING_SPEED                   = 900     # cps (half normal)

# --- Amplifier / motor configuration (2HA075520 amplifier) -------------------
MOTOR_TYPE      = -2.5  # MT: step motor, active-high step pulse
STEP_RESOLUTION = 2     # YA: 1=full, 2=half, 4=quarter, 8=eighth
LOW_CURRENT_ON  = 1     # LC: reduced holding current (0=off, 1=on)
AMP_GAIN        = 3     # AG: amplifier gain setting
MOTOR_SMOOTHING = 2.0   # YB

# CN (switch config): 1,1,-1,0,0
# Arg1=1 (latch), Arg2=1 (forward limit NC), Arg3=-1 (home switch active-low),
# Arg4=0, Arg5=0
CN_CONFIG = "1,1,-1,0,0"


# --- Unit helpers -------------------------------------------------------------

def counts_to_mm(axis_letter: str, counts: float) -> float:
    """Step counts -> physical position in mm for the given axis."""
    sps    = STEPS_PER_MM[axis_letter]
    offset = ZERO_OFFSET_COUNTS[axis_letter]
    return (counts - offset) / sps


def mm_to_counts(axis_letter: str, mm: float) -> int:
    """Physical position (mm) -> step counts for the given axis."""
    sps    = STEPS_PER_MM[axis_letter]
    offset = ZERO_OFFSET_COUNTS[axis_letter]
    return int(round(mm * sps + offset))


def cps_to_mm_per_sec(axis_letter: str, cps: float) -> float:
    """Counts-per-second -> mm/s."""
    return cps / STEPS_PER_MM[axis_letter]


def mm_per_sec_to_cps(axis_letter: str, mm_per_sec: float) -> int:
    """mm/s -> counts-per-second (rounded to int)."""
    return int(round(mm_per_sec * STEPS_PER_MM[axis_letter]))


# --- Log-amp model registry --------------------------------------------------
LOG_AMP_MODELS = {
    "2HA032380 (pos, 0-6V)": {"polarity": "pos", "v_at_1nA": 0.0, "v_at_1mA": 6.0},
    "2HA032382 (neg, 0-6V)": {"polarity": "neg", "v_at_1nA": 0.0, "v_at_1mA": 6.0},
    "2HA032390 (pos, 9-3V)": {"polarity": "pos", "v_at_1nA": 9.0, "v_at_1mA": 3.0},
    "2HA032392 (neg, 9-3V)": {"polarity": "neg", "v_at_1nA": 9.0, "v_at_1mA": 3.0},
}
DEFAULT_LOG_AMP_MODEL = "2HA032380 (pos, 0-6V)"

# LabJack T7 analog input -> human-readable jaw label
LABJACK_CHANNEL_MAP = {
    "AIN0": "X+",
    "AIN1": "X-",
    "AIN2": "Y+",
    "AIN3": "Y-",
}


# --- Self-test ---------------------------------------------------------------

if __name__ == "__main__":
    assert AXIS_LETTERS == ["A", "B", "C", "D"]
    assert AXIS_LABELS  == ["X+", "X-", "Y+", "Y-"]

    # 1 mm = 629.92126 counts (round-trip)
    for axis in AXIS_LETTERS:
        c = mm_to_counts(axis, 1.0)
        assert abs(c - 630) < 1, f"1 mm should be ~630 counts, got {c}"
        mm = counts_to_mm(axis, c)
        assert abs(mm - 1.0) < 0.002, f"Round-trip failed: {mm}"

    # mm/s ↔ cps
    for axis in AXIS_LETTERS:
        cps = mm_per_sec_to_cps(axis, 1.0)
        assert abs(cps - 630) < 1
        mms = cps_to_mm_per_sec(axis, cps)
        assert abs(mms - 1.0) < 0.002

    # Log-amp models
    for name, spec in LOG_AMP_MODELS.items():
        assert "polarity" in spec and "v_at_1nA" in spec and "v_at_1mA" in spec

    print("[OK] slit_config self-test passed")
    print(f"    STEPS_PER_MM = {STEPS_PER_MM['A']}")
    print(f"    1 mm = {mm_to_counts('A', 1.0)} counts")
    print(f"    1 mm/s = {mm_per_sec_to_cps('A', 1.0)} cps")
    print(f"    DEFAULT_SPEED = {DEFAULT_SPEED_COUNTS_PER_SEC} cps = "
          f"{cps_to_mm_per_sec('A', DEFAULT_SPEED_COUNTS_PER_SEC):.3f} mm/s")
