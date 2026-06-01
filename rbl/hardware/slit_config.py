"""
slit_config.py
Hardware mapping and calibration for the 4-jaw beam-line slits.

Galil DMC-4103 axis letters (A,B,C,D) -> physical slit jaws (X+, X-, Y+, Y-).
Steps-per-mm are placeholders; update by hand after first bench calibration.
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


# Steps per mm — set empirically. Convention: positive direction = retract
# jaw away from beam. Update after measuring on the bench.
STEPS_PER_MM = {
    "A": 1000.0,
    "B": 1000.0,
    "C": 1000.0,
    "D": 1000.0,
}

# Zero offset in counts — "where is mechanical zero of the jaw?"
# Set when you DP the axis at a known reference position.
ZERO_OFFSET_COUNTS = {
    "A": 0,
    "B": 0,
    "C": 0,
    "D": 0,
}

# Safe-default motion parameters (sent at startup)
DEFAULT_SPEED_COUNTS_PER_SEC   = 5000
DEFAULT_ACCEL_COUNTS_PER_SEC2  = 100000
DEFAULT_JOG_SPEED              = 2000


def counts_to_mm(axis_letter: str, counts: float) -> float:
    """Encoder counts -> physical position in mm for the given axis."""
    sps    = STEPS_PER_MM[axis_letter]
    offset = ZERO_OFFSET_COUNTS[axis_letter]
    return (counts - offset) / sps


def mm_to_counts(axis_letter: str, mm: float) -> int:
    """Physical position (mm) -> encoder counts for the given axis."""
    sps    = STEPS_PER_MM[axis_letter]
    offset = ZERO_OFFSET_COUNTS[axis_letter]
    return int(round(mm * sps + offset))


# --- Log-amp model registry --------------------------------------------------
# Maps NEC log-amp part number variants to their voltage-output range.
# Polarity refers to which polarity of input current the unit accepts.
LOG_AMP_MODELS = {
    "2HA032380 (pos, 0-6V)": {"polarity": "pos", "v_at_1nA": 0.0, "v_at_1mA": 6.0},
    "2HA032382 (neg, 0-6V)": {"polarity": "neg", "v_at_1nA": 0.0, "v_at_1mA": 6.0},
    "2HA032390 (pos, 9-3V)": {"polarity": "pos", "v_at_1nA": 9.0, "v_at_1mA": 3.0},
    "2HA032392 (neg, 9-3V)": {"polarity": "neg", "v_at_1nA": 9.0, "v_at_1mA": 3.0},
}
DEFAULT_LOG_AMP_MODEL = "2HA032380 (pos, 0-6V)"

# LabJack T7 analog input -> human-readable jaw label.
# Adjust if your DB9-to-T7 wiring is different.
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
    assert counts_to_mm("A", 1000) == 1.0
    assert mm_to_counts("A", 1.0)  == 1000
    # Round trip
    for counts in [-12345, 0, 999, 1_000_000]:
        for axis in AXIS_LETTERS:
            mm = counts_to_mm(axis, counts)
            assert mm_to_counts(axis, mm) == counts, f"Roundtrip failed: {axis}, {counts}"
    # All log-amp models are well-formed
    for name, spec in LOG_AMP_MODELS.items():
        assert "polarity" in spec and "v_at_1nA" in spec and "v_at_1mA" in spec
    print("[OK] slit_config self-test passed")
