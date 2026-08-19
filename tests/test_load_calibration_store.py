"""
Unit tests for rbl.config.load_calibration_store — no Qt, no hardware.
"""
import pytest

from rbl.config import load_calibration_store as store


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STORE_PATH", tmp_path / "load_calibration.json")
    return store


class TestSaveAndLoad:
    def test_missing_store_is_empty(self, isolated_store):
        assert isolated_store.load_all() == {}
        assert isolated_store.capacitance_pf_for("X+") is None
        assert isolated_store.measurement_for("X+") is None

    def test_round_trips_one_channel_one_condition(self, isolated_store):
        isolated_store.save_measurement("X+", c_pf=1180.0, g_us=0.02,
                                         load_condition="ON_PLATES", method="impedance_sweep")
        assert isolated_store.capacitance_pf_for("X+") == 1180.0
        assert isolated_store.capacitance_pf_for("X+", "ON_PLATES") == 1180.0
        assert isolated_store.capacitance_pf_for("X+", "DISCONNECTED") is None

    def test_other_channels_are_unaffected(self, isolated_store):
        isolated_store.save_measurement("X+", c_pf=1180.0, g_us=0.0,
                                         load_condition="ON_PLATES", method="m")
        assert isolated_store.capacitance_pf_for("X-") is None

    def test_record_carries_method_and_timestamp(self, isolated_store):
        isolated_store.save_measurement("X+", c_pf=1180.0, g_us=0.02,
                                         load_condition="ON_PLATES", method="impedance_sweep")
        record = isolated_store.measurement_for("X+", "ON_PLATES")
        assert record["method"] == "impedance_sweep"
        assert record["g_us"] == 0.02
        assert "measured_at" in record


class TestBothConditionsCoexist:
    """Section 2.4: DISCONNECTED and ON_PLATES must both be retrievable for
    the same channel — the whole point of the decomposition comparison."""

    def test_saving_one_condition_does_not_erase_the_other(self, isolated_store):
        isolated_store.save_measurement("X+", c_pf=1180.0, g_us=0.02,
                                         load_condition="ON_PLATES", method="m")
        isolated_store.save_measurement("X+", c_pf=125.0, g_us=0.0,
                                         load_condition="DISCONNECTED", method="m")
        assert isolated_store.capacitance_pf_for("X+", "ON_PLATES") == 1180.0
        assert isolated_store.capacitance_pf_for("X+", "DISCONNECTED") == 125.0

    def test_capacitance_pf_for_without_condition_returns_most_recent(self, isolated_store):
        isolated_store.save_measurement("X+", c_pf=125.0, g_us=0.0,
                                         load_condition="DISCONNECTED", method="m",
                                         measured_at=100.0)
        isolated_store.save_measurement("X+", c_pf=1180.0, g_us=0.02,
                                         load_condition="ON_PLATES", method="m",
                                         measured_at=200.0)
        assert isolated_store.capacitance_pf_for("X+") == 1180.0

    def test_comparison_for_returns_both_and_the_difference(self, isolated_store):
        isolated_store.save_measurement("X+", c_pf=1180.0, g_us=0.0,
                                         load_condition="ON_PLATES", method="m")
        isolated_store.save_measurement("X+", c_pf=125.0, g_us=0.0,
                                         load_condition="DISCONNECTED", method="m")
        cmp = isolated_store.comparison_for("X+")
        assert cmp["ON_PLATES"]["c_pf"] == 1180.0
        assert cmp["DISCONNECTED"]["c_pf"] == 125.0
        assert cmp["diff_c_pf"] == pytest.approx(1055.0)

    def test_comparison_for_missing_side_has_no_diff(self, isolated_store):
        isolated_store.save_measurement("X+", c_pf=1180.0, g_us=0.0,
                                         load_condition="ON_PLATES", method="m")
        cmp = isolated_store.comparison_for("X+")
        assert cmp["ON_PLATES"] is not None
        assert cmp["DISCONNECTED"] is None
        assert cmp["diff_c_pf"] is None

    def test_comparison_for_unmeasured_channel_is_all_none(self, isolated_store):
        cmp = isolated_store.comparison_for("Y-")
        assert cmp == {"DISCONNECTED": None, "ON_PLATES": None, "diff_c_pf": None}
