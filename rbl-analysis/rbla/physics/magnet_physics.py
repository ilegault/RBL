"""
magnet_physics.py
Bending-magnet field physics for the 45° switching magnet on the right beamline.

Data-only constants + one function, same style as deflection_physics.py.

Field formula (verified against "Right Beamline Magnet Calculator.xlsx", cell D3):

    B_gauss = 45.54 * sqrt(m_amu * E_keV) / (q * R_m)

where
    m_amu  = ion mass (amu)
    E_keV  = ion kinetic energy (keV)
    q      = charge state
    R_m    = bending radius = 0.615 m (fixed for this magnet)

Verification anchor: m=27, E=2000 keV, q=2, R=0.615 -> 8603.7 Gauss (matches D3).

Notes (from the spreadsheet's notes column, surfaced in the tab UI):
  * Max field is ~16-17 kGauss.
  * Typical energies: 1.5-3.0 MeV for 1+, 2.25-4.5 MeV for 2+.
  * 3+ charge states don't yield much beam current.
"""

# -- Magnet constants ----------------------------------------------------------
MAGNET_RADIUS_M = 0.615      # bending radius (m), fixed for this switching magnet
MAGNET_MAX_GAUSS = 16500.0   # saturates around 16-17 kGauss
MAGNET_CONSTANT = 45.54      # spreadsheet constant in B = k*sqrt(m*E)/(q*R)


def calculate_magnet_field(mass_amu, energy_keV, charge_state,
                           radius_m=MAGNET_RADIUS_M):
    """Return dict: field_gauss, pct_of_max, exceeds_max (bool).

    B_gauss = 45.54 * sqrt(m_amu * E_keV) / (q * R_m)
    """
    import math
    field_gauss = (MAGNET_CONSTANT * math.sqrt(mass_amu * energy_keV)
                   / (charge_state * radius_m))
    return {
        "field_gauss": field_gauss,
        "pct_of_max":  field_gauss / MAGNET_MAX_GAUSS * 100.0,
        "exceeds_max": field_gauss > MAGNET_MAX_GAUSS,
    }


# -- Surveyed reference rows ---------------------------------------------------
# These mirror the species/energy survey in the spreadsheet's H2:M24 block
# (H, He, C, Al, Ti, V, Fe, Ni, W). The source .xlsx is not committed to this
# repo, so the field_gauss values are produced by the SAME verified formula
# that reproduces D3 (8603.7 Gauss) exactly — they are internally consistent
# with the spreadsheet's own computed column. Treat these as reference DATA.
MAGNET_REFERENCE_ROWS = [
    {"species": "Proton (H+)",  "mass_amu":   1.008, "energy_keV": 2000, "charge_state": 1, "field_gauss": 3324.8},
    {"species": "Helium (He)",  "mass_amu":   4.003, "energy_keV": 2000, "charge_state": 1, "field_gauss": 6625.6},
    {"species": "Carbon (C)",   "mass_amu":  12.011, "energy_keV": 2000, "charge_state": 1, "field_gauss": 11476.8},
    {"species": "Aluminum (Al)","mass_amu":  26.982, "energy_keV": 2000, "charge_state": 2, "field_gauss": 8600.8},
    {"species": "Titanium (Ti)","mass_amu":  47.867, "energy_keV": 3000, "charge_state": 2, "field_gauss": 14030.3},
    {"species": "Vanadium (V)", "mass_amu":  50.942, "energy_keV": 3000, "charge_state": 2, "field_gauss": 14473.9},
    {"species": "Iron (Fe)",    "mass_amu":  55.845, "energy_keV": 3000, "charge_state": 2, "field_gauss": 15154.5},
    {"species": "Nickel (Ni)",  "mass_amu":  58.693, "energy_keV": 3000, "charge_state": 2, "field_gauss": 15536.1},
    {"species": "Tungsten (W)", "mass_amu": 183.84,  "energy_keV": 4500, "charge_state": 2, "field_gauss": 33675.5},
]


# -- Self-test -----------------------------------------------------------------

if __name__ == "__main__":
    import sys

    # Verification anchor: m=27, E=2000 keV, q=2, R=0.615 -> 8603.7 Gauss (D3)
    r = calculate_magnet_field(27, 2000, 2)
    ok1 = abs(r["field_gauss"] - 8603.7) < 1.0
    tag1 = "[OK ]" if ok1 else "[FAIL]"
    print(f"{tag1}  D3 anchor (m=27, E=2000 keV, q=2): "
          f"got {r['field_gauss']:.1f} Gauss, expected 8603.7")

    # exceeds_max flag: a high field should trip it
    r2 = calculate_magnet_field(183.84, 4500, 2)
    ok2 = r2["exceeds_max"] is True
    tag2 = "[OK ]" if ok2 else "[FAIL]"
    print(f"{tag2}  exceeds_max trips above {MAGNET_MAX_GAUSS:.0f} Gauss "
          f"(W 4.5 MeV 2+ = {r2['field_gauss']:.1f})")

    # pct_of_max sanity: anchor is ~52% of max
    ok3 = abs(r["pct_of_max"] - 8603.7 / MAGNET_MAX_GAUSS * 100.0) < 0.1
    tag3 = "[OK ]" if ok3 else "[FAIL]"
    print(f"{tag3}  pct_of_max = {r['pct_of_max']:.1f}%")

    print(f"[OK ]  MAGNET_REFERENCE_ROWS has {len(MAGNET_REFERENCE_ROWS)} rows")

    if not (ok1 and ok2 and ok3):
        print("\nMagnet physics checks FAILED")
        sys.exit(1)
    print("\nAll magnet physics checks passed.")
