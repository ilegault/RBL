"""
steerer_geometry.py
Geometry of THE steerer on this beamline — NEC P/N 2EA021441, S/N 14578.1124 —
and the drift from its exit to the sample.

WHY THIS EXISTS
---------------
`raster_model.py` takes plate length/gap as plain floats because it must stay
pure math (no config lookups — see its module docstring). Something still has
to hold this machine's actual numbers, and this is that something: data only,
no computation.

WHY THERE IS NO LONGER A DROPDOWN
---------------------------------
There used to be a table of every steerer in the NEC XY Steerer manual and a
combo box to pick from it. There is only one steerer in this beamline and it
is not going to change, so the picker could only ever do one of two things:
sit on the right answer, or be left on the wrong one. A geometry the operator
can mis-set is a silently wrong deflection calculation, which is the one
number this whole tab exists to produce. So the installed unit is a fact
here, not a choice there.

WHERE THE NUMBERS COME FROM
---------------------------
The lab's own reference sheet, `Hirst RHBL Deflection Information.xlsx`,
sheet "Deflection on Sample" — the numbers this beamline's deflection has
been quoted from all along. Its per-species formula reads

    I8 = ((C8*10^3) * 12.5 * q) / (2 * 3.8 * E*10^6) * C9 * 2

which is Section IV of the NEC manual with l = 12.5 cm, d = 3.8 cm, C8 the
PER-PLATE kV, C9 the drift in mm, and the trailing *2 the push-pull factor
(see the FACTOR-OF-TWO HAZARD note in raster_model.py — our functions take
the plate-to-plate voltage instead, which is the same thing said once).

NOTE ON 12.5 vs 12.7
--------------------
The manual's spec tables give 5" (12.7 cm) for every 12.7-class steerer, and
12.5 cm appears in the manual only in the Section IV worked example. The lab
sheet uses 12.5, so this file uses 12.5 — the app must not quietly disagree
with the number the beamline's results have been quoted against. It is a
0.4 % difference in deflection. If the plates are ever actually measured,
change PLATE_LENGTH_CM here and nothing else in the codebase moves.

The 5 kV per-plate rating is likewise the installed unit's, not the ES10
assembly's 10 kV — 2EA021441 is a customer variant (manual Section I: special
models are adaptations of the standard unit).
"""

# NEC part number stamped on the tube, and its serial.
STEERER_MODEL  = "2EA021441"
STEERER_SERIAL = "14578.1124"

# Plate geometry, per the lab deflection sheet.
PLATE_LENGTH_CM      = 12.5    # l in the deflection formula
PLATE_GAP_CM         = 3.8     # d in the deflection formula
PLATE_WIDTH_CM       = 10.2
ENTRANCE_APERTURE_CM = 2.54

# Manufacturer's rating, PER PLATE, with respect to the enclosure. The rig
# runs push-pull, so the plate-to-plate differential ceiling is twice this.
PLATE_RATING_KV        = 5.0
DIFFERENTIAL_RATING_KV = 2.0 * PLATE_RATING_KV

# ---------------------------------------------------------------------------
# Drift, steerer exit to sample.
#
# From the "TOTAL BEAMLINE LENGTH [in]" row of the same sheet, summing the
# components AFTER the steerer (its C9 = SUM(F4:N4) * 25.4):
#
#   DT ...................... 50.00 in
#   XYSL ....................  7.12
#   BPM80 ...................  7.22
#   FC50 ....................  7.25
#   Reducing nipple .........  2.50
#   GV ......................  2.89
#   Bellows .................  5.00
#   Adapter .................  2.50
#   Chamber, flange to sample 13.00
#                             ------
#                             97.48 in = 2475.99 mm
#
# This is the DEFAULT the Raster Planner opens on, not a hard constraint —
# the sample can sit somewhere else in the chamber, so the tab still lets it
# be changed.
# ---------------------------------------------------------------------------
DRIFT_TO_SAMPLE_IN = 97.48
DRIFT_TO_SAMPLE_CM = DRIFT_TO_SAMPLE_IN * 2.54     # 247.60 cm

# The drift tube alone, which the sheet also quotes on its own ("Deflection
# through DT"): the aperture the beam has to clear before anything else.
DRIFT_TUBE_CM = 50.0 * 2.54                        # 127.0 cm


def describe() -> str:
    """One line for the GUI, so the operator can see what was assumed."""
    return (f"NEC {STEERER_MODEL}  —  plates {PLATE_LENGTH_CM} cm long, "
            f"{PLATE_GAP_CM} cm gap, {PLATE_RATING_KV:.0f} kV/plate "
            f"({DIFFERENTIAL_RATING_KV:.0f} kV differential)")


if __name__ == "__main__":
    assert PLATE_LENGTH_CM > 0 and PLATE_GAP_CM > 0 and PLATE_RATING_KV > 0
    assert DIFFERENTIAL_RATING_KV == 2.0 * PLATE_RATING_KV
    print("[OK]", describe())
    print(f"[OK] drift to sample {DRIFT_TO_SAMPLE_CM:.2f} cm "
          f"({DRIFT_TO_SAMPLE_IN} in), drift tube {DRIFT_TUBE_CM:.1f} cm")
