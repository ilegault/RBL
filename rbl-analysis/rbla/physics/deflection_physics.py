"""
deflection_physics.py
Electrostatic deflector physics for the EEL5000 / XY Steerer beamline.

Plate geometry (XY Steerer manual, Hirst spreadsheet):
  l = 12.5 cm  (plate length)
  d = 3.8  cm  (plate separation)

Amplifier chain:
  Function generator  ->  EEL5000 amplifier (gain = 1000 V/V)  ->  deflector plates

Calibration anchor (from Hirst CSV / defaults.py):
  3 MeV proton, q = 1, V_plate = +/-9.5 kV  ->  25.79 mm deflection at sample
"""

# -- Plate geometry ------------------------------------------------------------
PLATE_LENGTH_MM = 125.0   # l = 12.5 cm
PLATE_GAP_MM    = 38.0    # d = 3.8  cm

# -- Amplifier chain limits ----------------------------------------------------
AMPLIFIER_GAIN   = 1000.0  # V/V  (EEL5000)
AMPLIFIER_MAX_KV = 5.0     # +/- kV  peak plate voltage before saturation
FG_MAX_VPP       = 20.0    # Vpp  DG1000Z max output (into 50 ohm)

# -- Default travel (steerer midpoint -> sample, mm) ---------------------------
# Derived from Hirst CSV anchor: 9.5 kV -> 25.79 mm @ 3 MeV proton
#   travel = defl * E_eV * d / (q * l * V_plate)
#          = 25.79 * 3e6 * 38 / (1 * 125 * 9500)  ~= 2476 mm
DEFAULT_TRAVEL_MM = 2476.0

# -- Ion species table ---------------------------------------------------------
# mass in atomic mass units (amu).  Charge state is set separately by the user.
SPECIES_TABLE = {
    "Proton (H+)":    {"mass":   1.008},
    "Helium (He)":    {"mass":   4.003},
    "Lithium (Li)":   {"mass":   6.941},
    "Boron (B)":      {"mass":  10.811},
    "Carbon (C)":     {"mass":  12.011},
    "Nitrogen (N)":   {"mass":  14.007},
    "Oxygen (O)":     {"mass":  15.999},
    "Neon (Ne)":      {"mass":  20.180},
    "Aluminum (Al)":  {"mass":  26.982},
    "Silicon (Si)":   {"mass":  28.085},
    "Argon (Ar)":     {"mass":  39.948},
    "Titanium (Ti)":  {"mass":  47.867},
    "Vanadium (V)":   {"mass":  50.942},
    "Chromium (Cr)":  {"mass":  51.996},
    "Iron (Fe)":      {"mass":  55.845},
    "Nickel (Ni)":    {"mass":  58.693},
    "Copper (Cu)":    {"mass":  63.546},
    "Zinc (Zn)":      {"mass":  65.38},
    "Krypton (Kr)":   {"mass":  83.798},
    "Silver (Ag)":    {"mass": 107.868},
    "Xenon (Xe)":     {"mass": 131.293},
    "Tungsten (W)":   {"mass": 183.84},
    "Gold (Au)":      {"mass": 196.967},
    "Custom":         {"mass":   1.000},
}

# -- Beamline points of interest (starter list; editable in the GUI) -----------
# distance_mm = distance from steerer plate midpoint to that point on the beamline.
# These are PLACEHOLDERS — replace with surveyed values. The sample anchor
# (2476 mm) is included so the new tab reproduces the old single-point result.
BEAMLINE_POIS = [
    # All coordinates are absolute image pixels (origin = top-left of image).
    # tip_x/tip_y : where the arrowhead lands on the photo.
    # label_x/label_y : where the text box sits (negative y = above image top edge).
    {"name": "Slit aperture",        "distance_mm": 1450.85,
     "tip_x": 757,  "tip_y": 45,  "label_x": 755,  "label_y": 0},
    {"name": "Beam Profile Monitor", "distance_mm": 1634.24,
     "tip_x": 819,  "tip_y": 80,  "label_x": 818,  "label_y": 35},
    {"name": "Faraday cup",          "distance_mm": 1818.39,
     "tip_x": 888,  "tip_y": 10,  "label_x": 888,  "label_y": -30},
    {"name": "Sample",               "distance_mm": 2475.99,
     "tip_x": 1127, "tip_y": 40,  "label_x": 1127, "label_y": -5},
]


def calculate_drive_for_deflection(
    deflection_mm: float,
    energy_MeV:   float,
    charge_state: int,
    travel_mm:    float = DEFAULT_TRAVEL_MM,
) -> dict:
    """
    Return the amplifier drive voltages required to produce *deflection_mm*
    at the sample for a beam with the given kinetic energy and charge state.

    Deflection formula (non-relativistic electrostatic steerer):
        theta = q * V_plate * l / (E_kin_eV * d)
        x     = theta * travel_mm         [small-angle, from plate mid-point]

    Solving for V_plate:
        V_plate [V] = x * E_kin_eV * d / (q * l * travel_mm)

    Parameters
    ----------
    deflection_mm : half-deflection at the sample (peak, one-sided), mm
    energy_MeV    : beam kinetic energy, MeV
    charge_state  : ion charge (integer multiple of e)
    travel_mm     : distance from centre of deflector plates to sample, mm

    Returns
    -------
    dict with keys:
        plate_kV          - peak per-plate voltage required (kV)
        fg_peak_V         - function-generator peak voltage required (V)
        fg_vpp_V          - function-generator Vpp required (V)
        exceeds_amplifier - True if plate_kV > AMPLIFIER_MAX_KV
        exceeds_fg        - True if fg_vpp_V > FG_MAX_VPP
    """
    E_eV = energy_MeV * 1e6  # kinetic energy in eV

    # Per-plate peak voltage (one plate at +V, the other at -V)
    V_plate_V = (deflection_mm * E_eV * PLATE_GAP_MM) / (
        charge_state * PLATE_LENGTH_MM * travel_mm
    )
    plate_kV = V_plate_V / 1000.0

    # Function-generator side (gain divides the required output)
    fg_peak_V = V_plate_V / AMPLIFIER_GAIN   # peak amplitude
    fg_vpp_V  = 2.0 * fg_peak_V              # peak-to-peak (sinusoidal drive)

    return {
        "plate_kV":          plate_kV,
        "fg_peak_V":         fg_peak_V,
        "fg_vpp_V":          fg_vpp_V,
        "exceeds_amplifier": plate_kV > AMPLIFIER_MAX_KV,
        "exceeds_fg":        fg_vpp_V > FG_MAX_VPP,
    }


def calculate_deflection_for_voltage(
    plate_kV:     float,
    energy_MeV:   float,
    charge_state: int,
    travel_mm:    float,
) -> float:
    """
    Forward direction: given a per-plate peak voltage, return the resulting
    one-sided deflection (mm) at distance *travel_mm* from the plate midpoint.

    Inverse of calculate_drive_for_deflection's plate_kV solve:
        x = q * V_plate * l * travel_mm / (E_eV * d)
    """
    E_eV      = energy_MeV * 1e6
    V_plate_V = plate_kV * 1000.0
    return (charge_state * V_plate_V * PLATE_LENGTH_MM * travel_mm) / (
        E_eV * PLATE_GAP_MM
    )


# -- Self-test -----------------------------------------------------------------

def _check(label, got, expected, tol=0.02):
    ok  = abs(got - expected) / max(abs(expected), 1e-12) < tol
    tag = "[OK ]" if ok else "[FAIL]"
    print(f"{tag}  {label}: got {got:.4g}, expected {expected:.4g}")
    return ok


if __name__ == "__main__":
    import sys

    # Calibration anchor: 3 MeV proton, 25.79 mm -> ~9.5 kV plate
    r = calculate_drive_for_deflection(25.79, 3.0, 1, DEFAULT_TRAVEL_MM)
    ok1 = _check("plate_kV  (3 MeV H, 25.79 mm)",  r["plate_kV"],  9.5)
    ok2 = _check("fg_peak_V (3 MeV H, 25.79 mm)",  r["fg_peak_V"], 9.5)
    ok3 = _check("fg_vpp_V  (3 MeV H, 25.79 mm)",  r["fg_vpp_V"],  19.0)

    # exceeds_amplifier: 9.5 kV > AMPLIFIER_MAX_KV (5 kV) limit -> True
    ok4 = _check("exceeds_amplifier flag (True)",
                 float(r["exceeds_amplifier"]), 1.0, tol=0.5)

    # exceeds_fg: 19 Vpp < 20 Vpp limit -> False
    ok5 = _check("exceeds_fg flag (False)",
                 float(r["exceeds_fg"]), 0.0, tol=0.5)

    # Doubling energy -> doubles required voltage
    r2  = calculate_drive_for_deflection(25.79, 6.0, 1, DEFAULT_TRAVEL_MM)
    ok6 = _check("plate_kV scales linearly with E",
                 r2["plate_kV"] / r["plate_kV"], 2.0)

    # Doubling charge state -> halves required voltage
    r3  = calculate_drive_for_deflection(25.79, 3.0, 2, DEFAULT_TRAVEL_MM)
    ok7 = _check("plate_kV scales as 1/q",
                 r["plate_kV"] / r3["plate_kV"], 2.0)

    # Species table
    print(f"[OK ]  SPECIES_TABLE has {len(SPECIES_TABLE)} entries: "
          f"{list(SPECIES_TABLE)}")
    ok8 = len(SPECIES_TABLE) >= 4

    # Forward/inverse round-trip: voltage -> deflection -> voltage
    _v_in = 3.0  # kV
    _x = calculate_deflection_for_voltage(_v_in, 3.0, 1, 2476.0)
    _r = calculate_drive_for_deflection(_x, 3.0, 1, 2476.0)
    ok9 = _check("round-trip plate_kV", _r["plate_kV"], _v_in)

    # Linearity in distance: 2x distance -> 2x deflection
    _x1 = calculate_deflection_for_voltage(3.0, 3.0, 1, 1000.0)
    _x2 = calculate_deflection_for_voltage(3.0, 3.0, 1, 2000.0)
    ok10 = _check("deflection linear in distance", _x2 / _x1, 2.0)

    ok11 = len(BEAMLINE_POIS) >= 2
    print(f"[OK ]  BEAMLINE_POIS has {len(BEAMLINE_POIS)} entries")

    n_fail = sum(not x for x in [ok1, ok2, ok3, ok4, ok5, ok6, ok7, ok8,
                                 ok9, ok10, ok11])
    if n_fail:
        print(f"\n{n_fail} check(s) FAILED")
        sys.exit(1)
    else:
        print("\nAll checks passed.")
