"""
Tests for rbl.services.calibration_writer.CalibrationWriter.

No hardware required — pure file I/O against a tmp_path directory.
"""
import csv
import json
import subprocess

import pytest

from rbl.services.calibration_writer import (
    CSV_COLUMNS, CalibrationWriter, config_snapshot, git_commit_hash,
)
from rbl.config.calibration_config import CAL_MAX_KV, LoadCondition


def _sample_row(i=0):
    return {
        "run_id": "cal_test", "timestamp_iso": "2026-01-01T00:00:00+00:00",
        "t_elapsed_s": float(i), "pass_index": 0, "pass_type": "up",
        "driven_amp": "X+", "commanded_kv": 1.0, "commanded_gen_v": 1.0,
        "ain": "AIN13", "amp_label": "X+", "kind": "voltage",
        "mean_v": 1.0, "std_v": 0.01, "min_v": 0.98, "max_v": 1.02,
        "n_samples": 400, "n_windows": 10,
        "converted_value": 1.0, "converted_unit": "kV",
        "stream_profile": "WAVEFORM",
    }


class TestCsvHeader:
    def test_header_matches_documented_columns(self, tmp_path):
        w = CalibrationWriter(run_id="cal_header", output_dir=tmp_path)
        w.close()
        with open(w.csv_path) as f:
            header = next(csv.reader(f))
        assert header == CSV_COLUMNS
        print("[OK] CSV header matches the documented column list exactly")


class TestCrashSurvival:
    def test_rows_survive_simulated_crash_mid_run(self, tmp_path):
        w = CalibrationWriter(run_id="cal_crash", output_dir=tmp_path)
        w.write_row(_sample_row(0))
        w.write_row(_sample_row(1))
        w.write_row(_sample_row(2))
        # Simulated crash: never call close(). Read the file back through a
        # SEPARATE handle, as a post-mortem inspection would.
        with open(w.csv_path) as f:
            reader = list(csv.DictReader(f))
        assert len(reader) == 3
        assert reader[0]["ain"] == "AIN13"
        assert reader[2]["t_elapsed_s"] == "2.0"
        print("[OK] rows survive a simulated crash mid-run (file readable, partial)")


class TestSidecar:
    def test_sidecar_round_trips_and_has_load_condition(self, tmp_path):
        w = CalibrationWriter(
            run_id="cal_meta", output_dir=tmp_path,
            metadata={"load_condition": LoadCondition.DISCONNECTED.value},
        )
        w.write_row(_sample_row(0))
        w.update_metadata(seed=42, operator_note="bench check")
        path = w.close()

        assert path == str(w.csv_path)
        with open(w.meta_path) as f:
            meta = json.load(f)
        assert meta["load_condition"] == "DISCONNECTED"
        assert meta["seed"] == 42
        assert meta["operator_note"] == "bench check"
        assert meta["run_id"] == "cal_meta"
        assert "start_timestamp_iso" in meta
        assert "end_timestamp_iso" in meta
        print("[OK] sidecar JSON round-trips and contains load_condition")

    def test_close_is_idempotent(self, tmp_path):
        w = CalibrationWriter(run_id="cal_idem", output_dir=tmp_path)
        p1 = w.close()
        p2 = w.close()
        assert p1 == p2

    def test_write_row_after_close_raises(self, tmp_path):
        w = CalibrationWriter(run_id="cal_after", output_dir=tmp_path)
        w.close()
        with pytest.raises(RuntimeError):
            w.write_row(_sample_row())


class TestConfigSnapshotAndGit:
    def test_config_snapshot_has_known_constants(self):
        snap = config_snapshot()
        assert "CAL_MAX_KV" in snap
        assert "CAL_UNCERTAINTY_V" in snap
        assert snap["CAL_MAX_KV"] == pytest.approx(CAL_MAX_KV)
        # CAL_OUTPUT_DIR is a Path -- must be JSON-serializable as str.
        assert isinstance(snap["CAL_OUTPUT_DIR"], str)

    def test_missing_git_binary_does_not_raise(self, monkeypatch):
        def _raise_not_found(*a, **k):
            raise FileNotFoundError("git not found")
        monkeypatch.setattr(subprocess, "run", _raise_not_found)
        result = git_commit_hash()
        assert result == ""
        print("[OK] missing git binary does not raise")

    def test_git_commit_hash_returns_string_normally(self):
        # Whatever the sandbox's git state is, this must never raise.
        result = git_commit_hash()
        assert isinstance(result, str)
