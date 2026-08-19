"""
Unit tests for rbl.config.steerer_geometry — no Qt, no hardware.
"""
from rbl.config.steerer_geometry import (
    STEERER_GEOMETRIES, DEFAULT_STEERER, plate_length_cm, plate_gap_cm, rating_kv,
)


def test_default_steerer_is_a_valid_key():
    assert DEFAULT_STEERER in STEERER_GEOMETRIES


def test_every_geometry_has_positive_dimensions_and_rating():
    for model, (length, gap, width, rating) in STEERER_GEOMETRIES.items():
        assert length > 0, model
        assert gap > 0, model
        assert rating > 0, model
        assert width is None or width > 0, model


def test_accessor_functions_match_the_table():
    for model in STEERER_GEOMETRIES:
        length, gap, _width, rating = STEERER_GEOMETRIES[model]
        assert plate_length_cm(model) == length
        assert plate_gap_cm(model) == gap
        assert rating_kv(model) == rating
