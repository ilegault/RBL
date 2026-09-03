"""
beam_species.py
The ion species this beamline actually runs, as the lab's own deflection
sheet lists them — the starting rows of the Raster Planner's species table.

WHY THIS EXISTS
---------------
`Hirst RHBL Deflection Information.xlsx`, sheet "Deflection on Sample",
carries a four-row species table (protons, Al, Ni, Ti) with a mass, an
energy and a charge state each. Those rows ARE the operating points this
beamline plans around, so the app opens on them instead of on one made-up
default the operator has to retype every session.

DEFAULTS, NOT CONSTRAINTS
-------------------------
Every field is editable in the tab, and rows can be changed freely. This
file only decides what is on screen the first time. Nothing downstream
looks a species up by name — the tab reads mass/energy/charge out of the
table, so an operator who retypes a row gets the retyped numbers with no
further ceremony.

WHY MASS IS CARRIED AT ALL
--------------------------
It does NOT enter the deflection formula — theta = V*l*q/(2*d*E) depends on
charge and energy only, and two ions at the same energy and charge state
deflect identically whatever they weigh. Mass is here because it is what
identifies the row to a human, it is what the switching-magnet calculation
needs (B = 45.54*sqrt(m*E)/(q*R)), and dropping it would make this table
stop matching the sheet it came from.
"""

# (name, mass_amu, energy_mev, charge_state)
DEFAULT_SPECIES = [
    ("Protons", 1.0,  3.00, 1),
    ("Al",      27.0, 2.25, 1),
    ("Ni",      57.0, 3.00, 3),
    ("Ti",      48.0, 3.40, 2),
]

# Which row the tab designs the drive for on first open.
DEFAULT_SPECIES_ROW = 0


if __name__ == "__main__":
    assert 0 <= DEFAULT_SPECIES_ROW < len(DEFAULT_SPECIES)
    for name, mass, energy, q in DEFAULT_SPECIES:
        assert name and mass > 0 and energy > 0 and q >= 1, name
    print(f"[OK] {len(DEFAULT_SPECIES)} species, default row "
          f"{DEFAULT_SPECIES[DEFAULT_SPECIES_ROW][0]!r}")
