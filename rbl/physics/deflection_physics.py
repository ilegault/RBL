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
    "Helium (He2+)":  {"mass":   4.003},
    "Carbon (C)":     {"mass":  12.011},
    "Iron (Fe)":      {"mass":  55.845},
    "Nickel (Ni)":    {"mass":  58.693},
    "Copper (Cu)":    {"mass":  63.546},
    "Gold (Au)":      {"mass": 196.967},
    "Custom":         {"mass":   1.000},
}


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

    # exceeds_amplifier: 9.5 kV < 10 kV limit -> False
    ok4 = _check("exceeds_amplifier flag (False)",
                 float(r["exceeds_amplifier"]), 0.0, tol=0.5)

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

    n_fail = sum(not x for x in [ok1, ok2, ok3, ok4, ok5, ok6, ok7, ok8])
    if n_fail:
        print(f"\n{n_fail} check(s) FAILED")
        sys.exit(1)
    else:
        print("\nAll checks passed.")
