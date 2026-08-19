"""
steerer_geometry.py
NEC XY Steerer geometries (XY Steerer Manual, Section VII) — the table the
operator picks from when telling raster_model.py which hardware is installed.

WHY THIS EXISTS
---------------
`raster_model.py` takes plate length/gap as plain floats because it must
stay pure math (no config lookups — see its module docstring). Something
still has to hold the manual's actual numbers so the GUI can offer a
dropdown instead of asking the operator to retype geometry by hand every
session, and this is that something: data only, no computation.
"""

# (plate_length_cm, plate_gap_cm, plate_width_cm or None, rating_kv)
STEERER_GEOMETRIES = {
    "ES5 (2EA003100)":              (12.7, 3.8, None, 5.0),
    "Single-axis 2EA032630":        (12.7, 3.8, 10.2, 5.0),
    "Single-axis 2EA055030":        (7.30, 3.8, 10.2, 5.0),
    "ES7 (2EA039291)":              (10.2, 3.2, None, 10.0),
    "ES10 (2EA021440)":             (12.7, 3.8, 10.2, 10.0),
    "Duo-axis 2EA068900":           (7.30, 3.8, None, 5.0),
}

DEFAULT_STEERER = "ES5 (2EA003100)"


def plate_length_cm(model: str) -> float:
    return STEERER_GEOMETRIES[model][0]


def plate_gap_cm(model: str) -> float:
    return STEERER_GEOMETRIES[model][1]


def rating_kv(model: str) -> float:
    return STEERER_GEOMETRIES[model][3]


if __name__ == "__main__":
    assert DEFAULT_STEERER in STEERER_GEOMETRIES
    for model, (length, gap, width, rating) in STEERER_GEOMETRIES.items():
        assert length > 0 and gap > 0 and rating > 0, model
        assert plate_length_cm(model) == length
        assert plate_gap_cm(model) == gap
        assert rating_kv(model) == rating
    print(f"[OK] {len(STEERER_GEOMETRIES)} steerer geometries validated")
