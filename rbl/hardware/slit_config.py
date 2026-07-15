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

# Physical gap offset in mm per jaw: the slits have a ~0.4 mm gap between them
# when both jaws are at their homed/zeroed position, so each jaw sits 0.2 mm
# from true centre. After the user zeros (DP=0), counts=0 displays as 0.2 mm.
MM_ZERO_OFFSET: dict[str, float] = {
    "A": 0.2,
    "B": 0.2,
    "C": 0.2,
    "D": 0.2,
}

# --- Motion parameters (per spec email) --------------------------------------
DEFAULT_SPEED_COUNTS_PER_SEC   = 1000
DEFAULT_ACCEL_COUNTS_PER_SEC2  = 25600
DEFAULT_JOG_SPEED              = 500   # cps

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
    """Step counts -> physical position in mm for the given axis.

    counts=0 (after user zeros) returns MM_ZERO_OFFSET (0.2 mm) because the
    jaw sits 0.2 mm from true centre when homed.
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

# --- EEL5000.20.100 HV amplifier monitors ------------------------------------
# Each amplifier exposes two front-panel BNC monitors:
#   VOLTAGE MONITOR : 1000:1  -> 1 V at the BNC == 1 kV at the HV output
#   CURRENT MONITOR : 1 V     == 10 mA drawn from the amplifier
#
# Wired to the CB37 terminal board on AIN4..AIN11. AIN0..AIN3 are reserved for
# the log amps on the T7 body terminals and MUST NOT be duplicated on the CB37.
#
# Range must be +/-10 V on all eight: the current monitor reaches +/-10 V during
# the 100 mA / 4 ms transient the amplifier is rated for. A narrower range clips.

AMP_LABELS = ["X+", "X-", "Y+", "Y-"]

# amp label -> {"voltage": AIN name, "current": AIN name}
AMP_CHANNEL_MAP = {
    "X+": {"voltage": "AIN4",  "current": "AIN5"},
    "X-": {"voltage": "AIN6",  "current": "AIN7"},
    "Y+": {"voltage": "AIN8",  "current": "AIN9"},
    "Y-": {"voltage": "AIN10", "current": "AIN11"},
}

# Flat, ordered list of every AIN the amplifier tab needs.
AMP_AIN_NAMES = [
    AMP_CHANNEL_MAP[lbl][kind]
    for lbl in AMP_LABELS
    for kind in ("voltage", "current")
]

# Scale factors (see EEL5000 manual, Specifications, p. 1-3)
VOLTAGE_MONITOR_KV_PER_VOLT = 1.0    # 1000:1 divider -> 1 V == 1 kV
CURRENT_MONITOR_MA_PER_VOLT = 10.0   # 1 V == 10 mA

# Display / sanity limits
AMP_MAX_KV     = 5.0    # amplifier rated +/-5 kV
AMP_MAX_MA_DC  = 20.0   # continuous DC rating
AMP_MAX_MA_PK  = 100.0  # 4 ms peak rating

# Plot colors, matched to the log-amp tab's palette for visual consistency.
AMP_COLORS = {
    "X+": "#e74c3c",   # red
    "X-": "#3498db",   # blue
    "Y+": "#c47a00",   # amber
    "Y-": "#1a7a1a",   # green
}

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

    # Log-amp models
    for name, spec in LOG_AMP_MODELS.items():
        assert "polarity" in spec and "v_at_1nA" in spec and "v_at_1mA" in spec

    # Amplifier monitor map
    assert AMP_LABELS == ["X+", "X-", "Y+", "Y-"]
    assert len(AMP_AIN_NAMES) == 8
    assert AMP_AIN_NAMES == ["AIN4", "AIN5", "AIN6", "AIN7",
                             "AIN8", "AIN9", "AIN10", "AIN11"]
    # No overlap with the log amps — this is the whole safety point.
    assert not (set(AMP_AIN_NAMES) & set(LABJACK_CHANNEL_MAP.keys())), \
        "Amplifier AINs collide with log-amp AINs!"
    assert len(ALL_AIN_NAMES) == 12
    assert len(set(ALL_AIN_NAMES)) == 12, "Duplicate AIN in ALL_AIN_NAMES"
    for lbl in AMP_LABELS:
        assert lbl in AMP_COLORS

    print("[OK] slit_config self-test passed")
    print(f"    STEPS_PER_MM = {STEPS_PER_MM['A']}")
    print(f"    1 mm = {mm_to_counts('A', 1.0)} counts")
    print(f"    1 mm/s = {mm_per_sec_to_cps('A', 1.0)} cps")
    print(f"    DEFAULT_SPEED = {DEFAULT_SPEED_COUNTS_PER_SEC} cps = "
          f"{cps_to_mm_per_sec('A', DEFAULT_SPEED_COUNTS_PER_SEC):.3f} mm/s")
