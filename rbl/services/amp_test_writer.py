"""
amp_test_writer.py
Run-folder writer for the amplifier test matrix.

Folder layout
-------------
data/amp_tests/
└── amt_20260806T143012__g1_1__trip_threshold_inrush/
    ├── metadata.json
    ├── summary.csv
    ├── notes.md
    └── raw/
        ├── G1.1__0_kv__AIN9__000.npz
        └── ...

AMT_CSV_COLUMNS is a strict superset of CalibrationWriter.CSV_COLUMNS — the first
20 columns are identical so that processing/analyze_calibration.py continues to
work unchanged on these files.
"""
import csv
import json
import logging
import re
import time
from dataclasses import asdict
from datetime import timezone, datetime
from pathlib import Path

import numpy as np

from rbl.config.amp_test_config import AMT_OUTPUT_DIR, AMT_SUMMARY_CSV, AMT_METADATA_JSON
from rbl.config.amp_test_matrix import TestSpec
from rbl.services.calibration_writer import (
    CSV_COLUMNS as _BASE_COLUMNS,
    config_snapshot,
    git_commit_hash,
    now_iso,
)
from rbl.services.raw_capture_writer import RawCaptureWriter

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Column schema — must begin with _BASE_COLUMNS verbatim
# ---------------------------------------------------------------------------

AMT_CSV_COLUMNS: list[str] = list(_BASE_COLUMNS) + [
    "test_id",
    "group_num",
    "group_name",
    "factor",
    "level_label",
    "level_value",
    "target_ain",
    "raw_file",
    "raw_truncated",
    "trip_flag",
    "expect_trip",
    "limits_hash",
    "peak_v",
    "rms_v",
    "sample_rate_hz",
    "operator_paced_ack",
]

# Verify superset invariant at import time.
assert AMT_CSV_COLUMNS[:len(_BASE_COLUMNS)] == list(_BASE_COLUMNS), (
    "AMT_CSV_COLUMNS must begin with calibration_writer.CSV_COLUMNS verbatim"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slugify(text: str) -> str:
    s = text.lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = s.strip("_")
    return s or "x"


def _new_run_id(spec: TestSpec) -> str:
    ts   = time.strftime("amt_%Y%m%dT%H%M%S")
    slug = _slugify(f"{spec.test_id}_{spec.title}")[:60]
    return f"{ts}__{slug}"


# ---------------------------------------------------------------------------
# AmpTestWriter
# ---------------------------------------------------------------------------

class AmpTestWriter:
    """Owns one run's summary CSV + metadata.json + notes.md + raw/ subfolder."""

    def __init__(self, spec: TestSpec, limits_hash: str,
                 metadata: dict = None, output_dir: Path = None):
        """
        Args:
            spec:         The TestSpec being run.
            limits_hash:  Short hash from measured_limits.hash_state() (or "").
            metadata:     Extra key-value pairs to merge into metadata.json.
            output_dir:   Override base dir (default: AMT_OUTPUT_DIR).
        """
        self._spec        = spec
        self._limits_hash = limits_hash
        self._closed      = False

        base = Path(output_dir) if output_dir is not None else AMT_OUTPUT_DIR
        run_id = _new_run_id(spec)
        self._run_dir = base / run_id
        self._run_dir.mkdir(parents=True, exist_ok=True)

        # Summary CSV
        csv_path = self._run_dir / AMT_SUMMARY_CSV
        self._file = open(csv_path, "w", newline="")
        self._csv  = csv.DictWriter(self._file, fieldnames=AMT_CSV_COLUMNS)
        self._csv.writeheader()
        self._file.flush()

        # metadata.json
        self._meta_path = self._run_dir / AMT_METADATA_JSON
        self._meta: dict = {
            "run_id":             run_id,
            "test_spec":          asdict(spec),
            "limits_hash":        limits_hash,
            "config_snapshot":    config_snapshot(),
            "git_commit_hash":    git_commit_hash(),
            "start_timestamp_iso": now_iso(),
            "aborted":            False,
            "abort_reason":       "",
        }
        if metadata:
            self._meta.update(metadata)
        self._flush_meta()

        # notes.md
        self._notes_path = self._run_dir / "notes.md"

        # RawCaptureWriter shared across this run
        self._raw_writer = RawCaptureWriter(self._run_dir, spec.test_id)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def run_dir(self) -> Path:
        return self._run_dir

    def raw_writer(self) -> RawCaptureWriter:
        return self._raw_writer

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    def write_row(self, row: dict) -> None:
        """Append one row and flush immediately.

        Missing columns receive "".  Extra keys in row are silently ignored.
        """
        if self._closed:
            raise RuntimeError(f"write_row after close() on {self._run_dir}")
        self._csv.writerow({col: row.get(col, "") for col in AMT_CSV_COLUMNS})
        self._file.flush()

    def update_metadata(self, **fields) -> None:
        """Merge additional fields into metadata.json and flush."""
        self._meta.update(fields)
        self._flush_meta()

    def write_note(self, text: str) -> None:
        """Append a timestamped line to notes.md."""
        ts = datetime.now(tz=timezone.utc).isoformat()
        with open(self._notes_path, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {text}\n")

    def close(self, aborted: bool = False, abort_reason: str = "") -> str:
        """Finalize CSV and metadata.json.  Idempotent; returns run_dir path."""
        if self._closed:
            return str(self._run_dir)
        self._closed = True

        try:
            self._file.close()
        except Exception:
            log.exception("close: failed closing CSV in %s", self._run_dir)

        self._meta["end_timestamp_iso"] = now_iso()
        self._meta["aborted"]           = aborted
        self._meta["abort_reason"]      = abort_reason
        self._flush_meta()
        return str(self._run_dir)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _flush_meta(self) -> None:
        try:
            with open(self._meta_path, "w", encoding="utf-8") as f:
                json.dump(self._meta, f, indent=2, default=str)
        except Exception:
            log.exception("flush_meta: failed writing %s", self._meta_path)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import tempfile, os
    from rbl.config.amp_test_matrix import by_id

    print("=== amp_test_writer self-test ===")

    spec = by_id("G1.1")

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        w = AmpTestWriter(spec, limits_hash="deadbeef",
                          metadata={"operator_note": "self-test", "load_condition": "ON_PLATES"},
                          output_dir=base)

        # write two synthetic rows
        base_row = {
            "run_id": "test", "timestamp_iso": now_iso(), "t_elapsed_s": 1.0,
            "pass_index": 0, "pass_type": "up", "driven_amp": "Y+",
            "commanded_kv": 1.0, "commanded_gen_v": 1.0,
            "ain": "AIN9", "amp_label": "Y+", "kind": "voltage",
            "mean_v": 0.5, "std_v": 0.01, "min_v": 0.48, "max_v": 0.52,
            "n_samples": 1250, "n_windows": 1,
            "converted_value": 0.5, "converted_unit": "kV", "stream_profile": "WAVEFORM",
            # amp-test extra columns
            "test_id": "G1.1", "group_num": 1, "group_name": "Trip threshold & inrush",
            "factor": "Y+", "level_label": "1.0 kV", "level_value": 1.0,
            "target_ain": "AIN9", "raw_file": "", "raw_truncated": False,
            "trip_flag": False, "expect_trip": True, "limits_hash": "deadbeef",
            "peak_v": 0.52, "rms_v": 0.50, "sample_rate_hz": 12500.0,
            "operator_paced_ack": True,
        }
        w.write_row(base_row)
        w.write_row({**base_row, "level_label": "2.0 kV", "level_value": 2.0})
        w.write_note("all good")

        run_dir = w.run_dir

        # close (idempotent)
        p1 = w.close()
        p2 = w.close()
        assert p1 == p2, "close() not idempotent"
        print("[OK] close() is idempotent")

        # folder layout
        assert (run_dir / AMT_SUMMARY_CSV).exists(), "summary.csv missing"
        assert (run_dir / AMT_METADATA_JSON).exists(), "metadata.json missing"
        assert (run_dir / "notes.md").exists(), "notes.md missing"
        assert (run_dir / "raw").is_dir(), "raw/ dir missing"
        print("[OK] folder layout correct")

        # CSV header
        with open(run_dir / AMT_SUMMARY_CSV, newline="") as f:
            reader = csv.DictReader(f)
            assert list(reader.fieldnames) == AMT_CSV_COLUMNS, \
                f"CSV header mismatch: {reader.fieldnames}"
            rows = list(reader)
        assert len(rows) == 2, f"expected 2 rows, got {len(rows)}"
        print("[OK] CSV header matches AMT_CSV_COLUMNS, 2 data rows")

        # first 20 columns are base schema
        assert AMT_CSV_COLUMNS[:len(_BASE_COLUMNS)] == list(_BASE_COLUMNS)
        print(f"[OK] first {len(_BASE_COLUMNS)} columns match base CSV_COLUMNS")

        # metadata.json
        with open(run_dir / AMT_METADATA_JSON) as f:
            meta = json.load(f)
        assert "test_spec" in meta, "test_spec missing from metadata"
        assert meta["test_spec"]["proves"] == spec.proves, "proves text missing"
        assert meta["limits_hash"] == "deadbeef"
        assert meta["aborted"] is False
        print("[OK] metadata.json contains test_spec with proves, limits_hash, aborted")

        # write_row after close raises
        try:
            w.write_row(base_row)
            assert False, "should have raised"
        except RuntimeError:
            print("[OK] write_row after close raises RuntimeError")

    print("\n[OK] amp_test_writer self-test passed")
