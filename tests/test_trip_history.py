"""
Unit tests for rbl.services.trip_history — no Qt, no hardware.
"""
from rbl.services.trip_history import append_trip, load_trip_history


class TestAppendAndLoad:
    def test_missing_file_reads_back_empty(self, tmp_path):
        p = tmp_path / "nope" / "trip_history.jsonl"
        assert load_trip_history(p) == []

    def test_round_trips_and_preserves_order(self, tmp_path):
        p = tmp_path / "trip_history.jsonl"
        append_trip({"label": "X+", "state": "amp_off"}, path=p)
        append_trip({"label": "Y-", "state": "current_limited"}, path=p)
        records = load_trip_history(p)
        assert len(records) == 2
        assert records[0]["label"] == "X+"
        assert records[1]["state"] == "current_limited"

    def test_creates_parent_directories(self, tmp_path):
        p = tmp_path / "a" / "b" / "c" / "trip_history.jsonl"
        append_trip({"label": "X+"}, path=p)
        assert p.exists()

    def test_corrupt_line_is_skipped_not_fatal(self, tmp_path):
        p = tmp_path / "trip_history.jsonl"
        append_trip({"label": "X+"}, path=p)
        with open(p, "a") as f:
            f.write("not json\n")
        append_trip({"label": "Y+"}, path=p)
        records = load_trip_history(p)
        assert len(records) == 2
        assert records[0]["label"] == "X+"
        assert records[1]["label"] == "Y+"

    def test_append_never_raises_on_unwritable_path(self):
        # A path a normal user can't create should be swallowed, not raised.
        append_trip({"label": "X+"}, path="/proc/nope/trip_history.jsonl")
