"""
Unit tests for rbl.config.steerer_geometry — no Qt, no hardware.

The steerer is a single installed unit, not a table of options, so these
tests pin the numbers of THAT unit against the manual.
"""
import pytest

from rbl.config import steerer_geometry as sg


def test_it_is_the_steerer_actually_on_the_beamline():
    assert sg.STEERER_MODEL == "2EA021441"
    assert sg.STEERER_SERIAL == "14578.1124"


def test_plate_geometry_matches_the_lab_deflection_sheet():
    # Hirst RHBL Deflection Information.xlsx, "Deflection on Sample":
    #   I8 = ((C8*10^3) * 12.5 * q) / (2 * 3.8 * E*10^6) * C9 * 2
    # 12.5 cm and 3.8 cm are baked into that formula; the per-plate rating
    # of this unit is 5 kV, not the ES10 assembly's 10 kV.
    assert sg.PLATE_LENGTH_CM == 12.5
    assert sg.PLATE_GAP_CM == 3.8
    assert sg.PLATE_WIDTH_CM == 10.2
    assert sg.ENTRANCE_APERTURE_CM == 2.54
    assert sg.PLATE_RATING_KV == 5.0


def test_drift_to_sample_matches_the_sheets_beamline_table():
    # SUM(F4:N4) of the TOTAL BEAMLINE LENGTH row - every component after
    # the steerer: DT 50 + XYSL 7.12 + BPM80 7.22 + FC50 7.25 + reducing
    # nipple 2.5 + GV 2.89 + bellows 5 + adapter 2.5 + chamber 13.
    assert sg.DRIFT_TO_SAMPLE_IN == pytest.approx(97.48)
    assert sg.DRIFT_TO_SAMPLE_CM == pytest.approx(247.5992)
    assert sg.DRIFT_TUBE_CM == pytest.approx(127.0)


def test_differential_rating_is_twice_the_per_plate_rating():
    # Push-pull drive: X+ at +V and X- at -V is 2V plate to plate.
    assert sg.DIFFERENTIAL_RATING_KV == 2.0 * sg.PLATE_RATING_KV


def test_dimensions_are_positive():
    for name in ("PLATE_LENGTH_CM", "PLATE_GAP_CM", "PLATE_WIDTH_CM",
                 "ENTRANCE_APERTURE_CM", "PLATE_RATING_KV"):
        assert getattr(sg, name) > 0, name


def test_describe_names_the_part_and_the_gap():
    text = sg.describe()
    assert sg.STEERER_MODEL in text
    assert "3.8" in text


def test_there_is_no_picker_left_to_get_wrong():
    assert not hasattr(sg, "STEERER_GEOMETRIES")
    assert not hasattr(sg, "DEFAULT_STEERER")
