"""
Ticket 35: the append-only amplifier assignment and hardware-change history.

Time is always passed in; nothing here reads a clock. The store path comes from
the autouse fixture in conftest (a temp file), read through rbl.config.paths.
"""
import json
from datetime import datetime, timedelta

import pytest

from rbl.config import amplifier_assignments as aa
from rbl.config import paths

DAY1 = datetime(2026, 1, 1, 9, 0)


def _day(n):
    return DAY1 + timedelta(days=n - 1)


INITIAL = {"X+": "SN-A", "X-": "SN-B", "Y+": "SN-C", "Y-": "SN-D"}
SWAPPED = {"X+": "SN-C", "X-": "SN-B", "Y+": "SN-A", "Y-": "SN-D"}


def _lines():
    p = paths.AMPLIFIER_ASSIGNMENTS_STORE
    return p.read_bytes().splitlines(keepends=True) if p.exists() else []


def test_assignment_follows_the_swap_by_date():
    aa.record_assignment(INITIAL, _day(1), "initial")
    aa.record_assignment(SWAPPED, _day(10), "swap", note="X and Y exchanged")
    assert aa.assignment_at(_day(5))["X+"] == "SN-A"
    assert aa.assignment_at(_day(11))["X+"] == "SN-C"
    assert aa.assignment_at(_day(11))["Y+"] == "SN-A"


def test_before_the_first_record_there_is_no_assignment():
    assert aa.assignment_at(_day(1)) is None
    aa.record_assignment(INITIAL, _day(3), "initial")
    assert aa.assignment_at(_day(2)) is None
    assert aa.assignment_at(_day(3)) == INITIAL


def test_a_mapping_that_misses_a_plate_is_refused_and_writes_nothing():
    aa.record_assignment(INITIAL, _day(1), "initial")
    before = _lines()
    three = {k: v for k, v in INITIAL.items() if k != "Y-"}
    with pytest.raises(ValueError):
        aa.record_assignment(three, _day(2), "swap")
    assert _lines() == before
    assert len(_lines()) == 1


@pytest.mark.parametrize("bad", [
    {**INITIAL, "Z+": "SN-E"},          # a fifth position
    {**INITIAL, "X+": ""},              # blank serial
    {**INITIAL, "X+": None},            # no serial
])
def test_other_malformed_mappings_are_refused(bad):
    with pytest.raises(ValueError):
        aa.record_assignment(bad, _day(1), "initial")
    assert _lines() == []


def test_an_unknown_kind_is_refused():
    with pytest.raises(ValueError):
        aa.record_assignment(INITIAL, _day(1), "hardware_change")
    assert _lines() == []


def test_current_assignment_ignores_records_after_now():
    aa.record_assignment(INITIAL, _day(1), "initial")
    aa.record_assignment(SWAPPED, _day(10), "swap")
    assert aa.current_assignment(_day(9)) == INITIAL
    assert aa.current_assignment(_day(10)) == SWAPPED


def test_latest_hardware_change_is_the_later_one_and_ignores_the_future():
    assert aa.latest_hardware_change(_day(50)) is None
    aa.record_hardware_change(_day(20), "new cable")
    aa.record_hardware_change(_day(5), "feedthrough cleaned")
    aa.record_hardware_change(_day(40), "future change")
    assert aa.latest_hardware_change(_day(30)) == _day(20)
    assert aa.latest_hardware_change(_day(2)) is None


def test_a_swap_is_not_a_hardware_change_record_and_vice_versa():
    aa.record_assignment(INITIAL, _day(1), "initial")
    aa.record_hardware_change(_day(2), "x")
    assert aa.current_assignment(_day(3)) == INITIAL
    assert aa.latest_hardware_change(_day(3)) == _day(2)


def test_a_corrupt_line_between_valid_records_is_skipped(caplog):
    aa.record_assignment(INITIAL, _day(1), "initial")
    with open(paths.AMPLIFIER_ASSIGNMENTS_STORE, "a", encoding="utf-8") as f:
        f.write("{this is not json\n")
    aa.record_hardware_change(_day(2), "after the corrupt line")
    with caplog.at_level("WARNING"):
        records = aa.history()
    assert [r["kind"] for r in records] == ["initial", "hardware_change"]
    assert any("skip" in m.lower() or "corrupt" in m.lower()
               for m in caplog.messages)


def test_history_is_empty_when_there_is_no_file():
    assert aa.history() == []
    assert aa.current_assignment(_day(1)) is None


def test_three_writes_make_three_lines_and_never_touch_the_first():
    aa.record_assignment(INITIAL, _day(1), "initial", note="first")
    first = _lines()[0]
    aa.record_hardware_change(_day(2), "second")
    aa.record_assignment(SWAPPED, _day(3), "swap")
    lines = _lines()
    assert len(lines) == 3
    assert lines[0] == first
    rec = json.loads(first)
    assert rec["kind"] == "initial" and rec["mapping"] == INITIAL
    assert rec["note"] == "first"


def test_the_store_is_read_through_paths_at_call_time(tmp_path, monkeypatch):
    elsewhere = tmp_path / "other" / "a.jsonl"
    monkeypatch.setattr(paths, "AMPLIFIER_ASSIGNMENTS_STORE", elsewhere)
    aa.record_assignment(INITIAL, _day(1), "initial")
    assert elsewhere.exists()


def test_timezone_aware_and_naive_times_can_be_mixed():
    from datetime import timezone
    aa.record_assignment(INITIAL, DAY1.replace(tzinfo=timezone.utc), "initial")
    assert aa.assignment_at(_day(2)) == INITIAL
    assert aa.assignment_at(_day(1) - timedelta(days=2)) is None
