"""
Ticket 36: characterization results are kept forever, and the queries that read
them answer per plate position and per amplifier.

Real files in the temp folder redirected by conftest; times are passed in.
"""
import json
from datetime import datetime, timedelta

import pytest

from rbl.config import amplifier_assignments as aa
from rbl.config import characterization_history as ch
from rbl.config import paths

DAY1 = datetime(2026, 3, 1, 12, 0, 0)


def day(n):
    return DAY1 + timedelta(days=n - 1)


A_AT_XP = {"X+": "SN-A", "X-": "SN-B", "Y+": "SN-C", "Y-": "SN-D"}
B_AT_XP = {"X+": "SN-B", "X-": "SN-A", "Y+": "SN-C", "Y-": "SN-D"}


def result(plate="X+", serial="SN-A", cond="ON_PLATES", method="impedance_sweep",
           c_pf=1600.0, **extra):
    r = {"plate_position": plate, "amplifier_serial": serial,
         "load_condition": cond, "method": method,
         "values": {"c_pf": c_pf, "g_us": 0.5}}
    r.update(extra)
    return r


def test_the_newest_result_wins_and_carries_its_age():
    aa.record_assignment(A_AT_XP, day(1), "initial")
    ch.write_result(result(c_pf=1500.0), day(1))
    ch.write_result(result(c_pf=1650.0), day(3))
    got = ch.newest("X+", "ON_PLATES", day(4))
    assert got["values"]["c_pf"] == 1650.0
    assert got["age_s"] == 86400


def test_after_a_swap_the_old_result_belongs_to_the_old_amplifier():
    aa.record_assignment(A_AT_XP, day(1), "initial")
    ch.write_result(result(serial="SN-A", c_pf=1600.0), day(1))
    aa.record_assignment(B_AT_XP, day(2), "swap")
    assert ch.newest("X+", "ON_PLATES", day(3)) is None
    kept = ch.newest_for_amplifier("SN-A", "ON_PLATES", day(3))
    assert kept["values"]["c_pf"] == 1600.0
    assert kept["plate_position"] == "X+"


def test_results_by_amplifier_ignore_where_it_was_plugged_in():
    aa.record_assignment(A_AT_XP, day(1), "initial")
    ch.write_result(result(plate="X+", serial="SN-A", c_pf=1500.0), day(1))
    aa.record_assignment(B_AT_XP, day(2), "swap")
    ch.write_result(result(plate="X-", serial="SN-A", c_pf=1700.0), day(3))
    assert ch.newest_for_amplifier("SN-A", "ON_PLATES", day(4))["values"]["c_pf"] == 1700.0


def test_results_before_and_after_a_hardware_change_are_flagged():
    aa.record_assignment(A_AT_XP, day(1), "initial")
    ch.write_result(result(c_pf=1500.0), day(1))
    aa.record_hardware_change(day(2), "new cable")
    ch.write_result(result(c_pf=1700.0), day(3))
    old = ch.newest_for_amplifier("SN-A", "ON_PLATES", day(2) + timedelta(hours=1))
    assert old["predates_hardware_change"] is True
    assert ch.newest("X+", "ON_PLATES", day(4))["predates_hardware_change"] is False


def test_no_hardware_change_means_nothing_predates_it():
    aa.record_assignment(A_AT_XP, day(1), "initial")
    ch.write_result(result(), day(1))
    assert ch.newest("X+", "ON_PLATES", day(2))["predates_hardware_change"] is False


def test_two_results_in_the_same_second_make_two_files():
    aa.record_assignment(A_AT_XP, day(1), "initial")
    p1 = ch.write_result(result(c_pf=1500.0), day(2))
    p2 = ch.write_result(result(c_pf=1600.0), day(2))
    assert p1 != p2
    assert p2.name.endswith("_2.json")
    assert p1.exists() and p2.exists()
    assert json.loads(p1.read_text())["values"]["c_pf"] == 1500.0


def test_file_name_and_location():
    p = ch.write_result(result(), datetime(2026, 3, 1, 12, 30, 45))
    assert p.parent == paths.CHARACTERIZATION_DIR
    assert p.name == "X+_impedance_sweep_20260301T123045.json"


def test_the_file_records_when_and_the_assignment_in_force():
    aa.record_assignment(A_AT_XP, day(1), "initial")
    p = ch.write_result(result(), day(2))
    rec = json.loads(p.read_text())
    assert rec["when"] == day(2).isoformat()
    assert rec["assignment"] == A_AT_XP
    assert rec["plate_position"] == "X+"


def test_an_aborted_result_is_never_the_newest_and_is_still_on_disk():
    aa.record_assignment(A_AT_XP, day(1), "initial")
    ch.write_result(result(c_pf=1500.0), day(1))
    aborted = ch.write_result(
        result(c_pf=9999.0, aborted=True, abort_rule="leakage", abort_rung=3), day(2))
    assert aborted.exists()
    assert ch.newest("X+", "ON_PLATES", day(3))["values"]["c_pf"] == 1500.0
    assert ch.newest_for_amplifier("SN-A", "ON_PLATES", day(3))["values"]["c_pf"] == 1500.0


def test_on_plates_capacitance_ignores_other_load_conditions():
    aa.record_assignment(A_AT_XP, day(1), "initial")
    ch.write_result(result(cond="ON_PLATES", c_pf=1600.0), day(1))
    ch.write_result(result(cond="CABLE_ONLY", c_pf=300.0), day(2))
    ch.write_result(result(cond="DISCONNECTED", c_pf=100.0), day(3))
    assert ch.newest_on_plates_c_pf("X+", day(4)) == 1600.0
    assert ch.newest("X+", "CABLE_ONLY", day(4))["values"]["c_pf"] == 300.0


def test_on_plates_capacitance_is_none_when_not_measured():
    aa.record_assignment(A_AT_XP, day(1), "initial")
    ch.write_result(result(cond="CABLE_ONLY"), day(1))
    assert ch.newest_on_plates_c_pf("X+", day(2)) is None
    assert ch.newest_on_plates_c_pf("Y-", day(2)) is None


def test_a_newer_clamp_result_does_not_hide_the_capacitance():
    aa.record_assignment(A_AT_XP, day(1), "initial")
    ch.write_result(result(c_pf=1600.0), day(1))
    ch.write_result({"plate_position": "X+", "amplifier_serial": "SN-A",
                     "load_condition": "ON_PLATES", "method": "clamp_test",
                     "values": {"clamp_ma": 17.5}}, day(2))
    assert ch.newest_on_plates_c_pf("X+", day(3)) == 1600.0


def test_with_no_assignment_on_record_unassigned_results_count():
    ch.write_result(result(serial="unassigned", c_pf=1550.0), day(1))
    assert ch.newest("X+", "ON_PLATES", day(2))["values"]["c_pf"] == 1550.0
    assert ch.newest_on_plates_c_pf("X+", day(2)) == 1550.0


def test_once_an_assignment_exists_unassigned_results_are_nobodys():
    ch.write_result(result(serial="unassigned", c_pf=1550.0), day(1))
    aa.record_assignment(A_AT_XP, day(2), "initial")
    assert ch.newest("X+", "ON_PLATES", day(3)) is None


def test_a_result_dated_after_now_is_not_yet_known():
    aa.record_assignment(A_AT_XP, day(1), "initial")
    ch.write_result(result(c_pf=1500.0), day(2))
    ch.write_result(result(c_pf=1700.0), day(10))
    assert ch.newest("X+", "ON_PLATES", day(5))["values"]["c_pf"] == 1500.0


def test_nothing_on_disk_is_simply_not_measured():
    assert ch.newest("X+", "ON_PLATES", day(1)) is None
    assert ch.newest_for_amplifier("SN-A", "ON_PLATES", day(1)) is None
    assert ch.newest_on_plates_c_pf("X+", day(1)) is None


def test_a_corrupt_file_is_skipped_not_fatal(caplog):
    aa.record_assignment(A_AT_XP, day(1), "initial")
    ch.write_result(result(c_pf=1500.0), day(1))
    (paths.CHARACTERIZATION_DIR / "broken.json").write_text("{nope", encoding="utf-8")
    with caplog.at_level("WARNING"):
        assert ch.newest("X+", "ON_PLATES", day(2))["values"]["c_pf"] == 1500.0
    assert any("broken.json" in m for m in caplog.messages)


@pytest.mark.parametrize("bad", [
    {"plate_position": "Z+"},
    {"load_condition": "UNDER_WATER"},
    {"method": "guess"},
    {"values": None},
    {"amplifier_serial": ""},
])
def test_a_malformed_result_is_refused_and_writes_nothing(bad):
    r = {**result(), **bad}
    with pytest.raises(ValueError):
        ch.write_result(r, day(1))
    assert not paths.CHARACTERIZATION_DIR.exists() or not list(
        paths.CHARACTERIZATION_DIR.glob("*.json"))


def test_a_missing_key_is_refused():
    r = result()
    del r["method"]
    with pytest.raises(ValueError):
        ch.write_result(r, day(1))


def test_the_callers_dict_is_not_modified():
    aa.record_assignment(A_AT_XP, day(1), "initial")
    r = result()
    before = json.dumps(r, sort_keys=True)
    ch.write_result(r, day(2))
    assert json.dumps(r, sort_keys=True) == before
