"""
Unit tests for rbl.services.conditioning_history — no Qt, no hardware.
"""
from rbl.services.conditioning_history import append_session, load_sessions, sessions_for


class TestAppendAndLoad:
    def test_missing_file_reads_back_empty(self, tmp_path):
        p = tmp_path / "nope" / "conditioning_history.jsonl"
        assert load_sessions(p) == []

    def test_round_trips_and_preserves_order(self, tmp_path):
        p = tmp_path / "conditioning_history.jsonl"
        append_session({"amp_label": "X+", "achieved_kv": 2.0}, path=p)
        append_session({"amp_label": "Y-", "achieved_kv": 1.0}, path=p)
        records = load_sessions(p)
        assert len(records) == 2
        assert records[0]["amp_label"] == "X+"
        assert records[1]["amp_label"] == "Y-"

    def test_corrupt_line_is_skipped_not_fatal(self, tmp_path):
        p = tmp_path / "conditioning_history.jsonl"
        append_session({"amp_label": "X+"}, path=p)
        with open(p, "a") as f:
            f.write("not json\n")
        append_session({"amp_label": "Y+"}, path=p)
        records = load_sessions(p)
        assert len(records) == 2


class TestSessionsFor:
    def test_filters_to_one_channel_preserving_order(self, tmp_path):
        p = tmp_path / "conditioning_history.jsonl"
        append_session({"amp_label": "X+", "achieved_kv": 2.0}, path=p)
        append_session({"amp_label": "Y-", "achieved_kv": 1.0}, path=p)
        append_session({"amp_label": "X+", "achieved_kv": 3.5}, path=p)
        x_sessions = sessions_for("X+", path=p)
        assert [s["achieved_kv"] for s in x_sessions] == [2.0, 3.5]

    def test_no_sessions_for_unmeasured_channel(self, tmp_path):
        p = tmp_path / "conditioning_history.jsonl"
        append_session({"amp_label": "X+"}, path=p)
        assert sessions_for("Y-", path=p) == []
