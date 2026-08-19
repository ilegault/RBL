"""
Unit tests for rbl.services.dynamic_adjustment_history — no Qt, no hardware.
"""
import math

from rbl.services.dynamic_adjustment_history import (
    append_trial, load_trials, trials_for, winner_for,
)


class TestAppendAndLoad:
    def test_missing_file_reads_back_empty(self, tmp_path):
        p = tmp_path / "nope" / "dynamic_adjustment_history.jsonl"
        assert load_trials(p) == []

    def test_round_trips_and_preserves_order(self, tmp_path):
        p = tmp_path / "dynamic_adjustment_history.jsonl"
        append_trial({"amp_label": "X+", "pot_position": "A"}, path=p)
        append_trial({"amp_label": "Y-", "pot_position": "B"}, path=p)
        records = load_trials(p)
        assert len(records) == 2
        assert records[0]["pot_position"] == "A"
        assert records[1]["pot_position"] == "B"

    def test_corrupt_line_is_skipped_not_fatal(self, tmp_path):
        p = tmp_path / "dynamic_adjustment_history.jsonl"
        append_trial({"amp_label": "X+"}, path=p)
        with open(p, "a") as f:
            f.write("not json\n")
        append_trial({"amp_label": "Y+"}, path=p)
        assert len(load_trials(p)) == 2


class TestTrialsFor:
    def test_filters_to_one_channel_preserving_order(self, tmp_path):
        p = tmp_path / "dynamic_adjustment_history.jsonl"
        append_trial({"amp_label": "X+", "pot_position": "1"}, path=p)
        append_trial({"amp_label": "Y-", "pot_position": "2"}, path=p)
        append_trial({"amp_label": "X+", "pot_position": "3"}, path=p)
        assert [t["pot_position"] for t in trials_for("X+", path=p)] == ["1", "3"]

    def test_no_trials_for_unmeasured_channel(self, tmp_path):
        p = tmp_path / "dynamic_adjustment_history.jsonl"
        append_trial({"amp_label": "X+"}, path=p)
        assert trials_for("Y-", path=p) == []


class TestWinnerFor:
    def test_picks_lowest_figure_of_merit(self, tmp_path):
        p = tmp_path / "dynamic_adjustment_history.jsonl"
        append_trial({"amp_label": "X+", "pot_position": "1", "figure_of_merit": 5.0}, path=p)
        append_trial({"amp_label": "X+", "pot_position": "2", "figure_of_merit": 2.0}, path=p)
        append_trial({"amp_label": "X+", "pot_position": "3", "figure_of_merit": 8.0}, path=p)
        winner = winner_for("X+", path=p)
        assert winner["pot_position"] == "2"

    def test_no_trials_returns_none(self, tmp_path):
        p = tmp_path / "dynamic_adjustment_history.jsonl"
        assert winner_for("X+", path=p) is None

    def test_nan_scores_are_excluded(self, tmp_path):
        p = tmp_path / "dynamic_adjustment_history.jsonl"
        append_trial({"amp_label": "X+", "pot_position": "1", "figure_of_merit": float("nan")}, path=p)
        append_trial({"amp_label": "X+", "pot_position": "2", "figure_of_merit": 3.0}, path=p)
        winner = winner_for("X+", path=p)
        assert winner["pot_position"] == "2"

    def test_all_nan_scores_returns_none(self, tmp_path):
        p = tmp_path / "dynamic_adjustment_history.jsonl"
        append_trial({"amp_label": "X+", "pot_position": "1", "figure_of_merit": float("nan")}, path=p)
        assert winner_for("X+", path=p) is None
