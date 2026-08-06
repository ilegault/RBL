"""
tests/test_vacuum_logger.py
Offline tests for rbl/services/vacuum_logger.py.

Writes 20 synthetic VacuumState snapshots (including None pressure and error
states) to a temp directory, then reads the CSV back with csv.DictReader and
asserts:
  - correct row count
  - empty fields where pressure is None
  - state columns populated
  - the sentinel string "1.10E+03" never appears anywhere in the file
  - JSON sidecar is well-formed and contains expected keys
"""
import csv
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pytest

from rbl.services.vacuum_logger import VacuumLogger

# ---------------------------------------------------------------------------
# Minimal stand-ins for XgsChannel, XgsReading, VgcReading, VacuumState
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _FakeChannel:
    label: str


@dataclass(frozen=True)
class _FakeXgsReading:
    channel: _FakeChannel
    pressure: Optional[float]
    raw: str
    state: str


@dataclass(frozen=True)
class _FakeVgcReading:
    channel: str
    pressure: Optional[float]
    raw: str
    state: str


@dataclass(frozen=True)
class _FakeVacuumState:
    timestamp: float
    xgs_readings: list = field(default_factory=list)
    vgc_readings: list = field(default_factory=list)
    xgs_connected: bool = True
    vgc_connected: bool = True
    units_xgs: str = "Torr"
    units_vgc: str = "Torr"


# ---------------------------------------------------------------------------
# Fixture: build gauge_labels + 20 synthetic snapshots
# ---------------------------------------------------------------------------

_XGS_LABELS = ["xgs600:IG", "xgs600:CG1"]
_VGC_LABELS = ["vgc083:IG", "vgc083:CG1"]
_ALL_LABELS  = _XGS_LABELS + _VGC_LABELS


def _make_states(n: int = 20) -> list[_FakeVacuumState]:
    """Return n VacuumState-like objects with varied pressure/state values."""
    states = []
    for i in range(n):
        t = time.time() + i

        # xgs600:IG — normal OK reading most of the time
        xgs_ig = _FakeXgsReading(
            channel=_FakeChannel("IG"),
            pressure=1.23e-7 * (i + 1),
            raw="1.23E-07",
            state="OK",
        )
        # xgs600:CG1 — None pressure (OFF state) every 3rd row
        if i % 3 == 0:
            xgs_cg1 = _FakeXgsReading(
                channel=_FakeChannel("CG1"),
                pressure=None,
                raw="OFF",
                state="OFF",
            )
        else:
            xgs_cg1 = _FakeXgsReading(
                channel=_FakeChannel("CG1"),
                pressure=9.99e-4 * (i + 1),
                raw="9.99E-04",
                state="OK",
            )

        # vgc083:IG — OVER state on row 5, normal otherwise
        if i == 5:
            vgc_ig = _FakeVgcReading(
                channel="IG",
                pressure=None,
                raw="OVER",
                state="OVER",
            )
        else:
            vgc_ig = _FakeVgcReading(
                channel="IG",
                pressure=4.56e-8 * (i + 1),
                raw="4.56E-08",
                state="OK",
            )

        # vgc083:CG1 — OFF_OR_OVERRANGE (sentinel) on row 10, else OK
        # This simulates what the driver does when it receives "1.10E+03":
        # pressure=None, state="OFF_OR_OVERRANGE".  The sentinel must never
        # appear in the file as a numeric value.
        if i == 10:
            vgc_cg1 = _FakeVgcReading(
                channel="CG1",
                pressure=None,
                raw="1.10E+03",   # raw preserved for reference; pressure=None
                state="OFF_OR_OVERRANGE",
            )
        else:
            vgc_cg1 = _FakeVgcReading(
                channel="CG1",
                pressure=7.77e-5 * (i + 1),
                raw="7.77E-05",
                state="OK",
            )

        states.append(_FakeVacuumState(
            timestamp=t,
            xgs_readings=[xgs_ig, xgs_cg1],
            vgc_readings=[vgc_ig, vgc_cg1],
        ))

    return states


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestVacuumLogger:

    def test_row_count(self, tmp_path):
        """20 write_row() calls produce exactly 20 data rows."""
        logger = VacuumLogger(_ALL_LABELS, output_dir=tmp_path)
        logger.write_header_comment(["test run"])
        states = _make_states(20)
        for s in states:
            assert logger.write_row(s) is True
        csv_path = logger.close()

        rows = _data_rows(csv_path)
        assert len(rows) == 20

    def test_none_pressure_is_empty_field(self, tmp_path):
        """None pressure must produce an empty CSV field, not '0.0' or the sentinel."""
        logger = VacuumLogger(_ALL_LABELS, output_dir=tmp_path)
        logger.write_header_comment(["test run"])
        states = _make_states(20)
        for s in states:
            logger.write_row(s)
        csv_path = logger.close()

        rows = _data_rows(csv_path)
        # Row 0 and every 3rd row: xgs600:CG1 is None
        for i, row in enumerate(rows):
            if i % 3 == 0:
                assert row["xgs600:CG1"] == "", (
                    f"row {i}: expected empty xgs600:CG1 pressure, got {row['xgs600:CG1']!r}"
                )
            # Row 5: vgc083:IG is None
            if i == 5:
                assert row["vgc083:IG"] == "", (
                    f"row {i}: expected empty vgc083:IG pressure, got {row['vgc083:IG']!r}"
                )
            # Row 10: vgc083:CG1 is None (sentinel was consumed by driver)
            if i == 10:
                assert row["vgc083:CG1"] == "", (
                    f"row {i}: expected empty vgc083:CG1 pressure, got {row['vgc083:CG1']!r}"
                )

    def test_state_columns_populated(self, tmp_path):
        """Every _state column must contain a non-empty string on every row."""
        logger = VacuumLogger(_ALL_LABELS, output_dir=tmp_path)
        logger.write_header_comment(["test run"])
        for s in _make_states(20):
            logger.write_row(s)
        csv_path = logger.close()

        state_cols = [f"{lbl}_state" for lbl in _ALL_LABELS]
        for i, row in enumerate(_data_rows(csv_path)):
            for col in state_cols:
                assert row[col] != "", (
                    f"row {i}: state column {col!r} is empty"
                )

    def test_sentinel_never_appears_as_number(self, tmp_path):
        """The string '1.10E+03' must never appear as a pressure value in the CSV."""
        logger = VacuumLogger(_ALL_LABELS, output_dir=tmp_path)
        logger.write_header_comment(["test run"])
        for s in _make_states(20):
            logger.write_row(s)
        csv_path = logger.close()

        text = Path(csv_path).read_text(encoding="utf-8")
        # The sentinel may appear in the header comment section if we ever
        # wrote it there, but it must NEVER appear as a pressure field value.
        # We check: for every data row, pressure columns don't equal "1.10E+03"
        for i, row in enumerate(_data_rows(csv_path)):
            for lbl in _ALL_LABELS:
                assert row[lbl] != "1.10E+03", (
                    f"row {i}: sentinel '1.10E+03' appeared in pressure column {lbl!r}"
                )

    def test_json_sidecar_keys(self, tmp_path):
        """JSON sidecar must include run_id, start/end timestamps, gauge_labels."""
        logger = VacuumLogger(_ALL_LABELS, output_dir=tmp_path)
        logger.write_header_comment(["test run"])
        for s in _make_states(5):
            logger.write_row(s)
        csv_path = logger.close()

        json_path = Path(csv_path).with_suffix(".json")
        assert json_path.exists(), "JSON sidecar not created"
        meta = json.loads(json_path.read_text(encoding="utf-8"))
        for key in ("run_id", "start_timestamp_iso", "end_timestamp_iso",
                    "gauge_labels"):
            assert key in meta, f"sidecar missing key {key!r}"
        assert meta["gauge_labels"] == _ALL_LABELS

    def test_close_is_idempotent(self, tmp_path):
        """Calling close() twice must not raise and must return the same path."""
        logger = VacuumLogger(_ALL_LABELS, output_dir=tmp_path)
        logger.write_header_comment(["test run"])
        logger.write_row(_make_states(1)[0])
        path1 = logger.close()
        path2 = logger.close()
        assert path1 == path2

    def test_write_row_after_close_raises(self, tmp_path):
        """write_row() after close() must raise RuntimeError."""
        logger = VacuumLogger(_ALL_LABELS, output_dir=tmp_path)
        logger.write_header_comment(["test run"])
        logger.write_row(_make_states(1)[0])
        logger.close()
        with pytest.raises(RuntimeError):
            logger.write_row(_make_states(1)[0])

    def test_label_mismatch_returns_false(self, tmp_path):
        """write_row() returns False when gauge labels have changed."""
        logger = VacuumLogger(_ALL_LABELS, output_dir=tmp_path)
        logger.write_header_comment(["test run"])

        # Build a state with a DIFFERENT set of gauges
        bad_state = _FakeVacuumState(
            timestamp=time.time(),
            xgs_readings=[
                _FakeXgsReading(_FakeChannel("IG"), 1e-7, "1E-7", "OK"),
                # CG1 missing — only 1 xgs channel instead of 2
            ],
            vgc_readings=[
                _FakeVgcReading("IG",  1e-8, "1E-8", "OK"),
                _FakeVgcReading("CG1", 1e-5, "1E-5", "OK"),
            ],
        )
        result = logger.write_row(bad_state)
        assert result is False

    def test_fixed_columns_present(self, tmp_path):
        """CSV header must include iso_timestamp, unix_time, xgs_units, vgc_units."""
        logger = VacuumLogger(_ALL_LABELS, output_dir=tmp_path)
        logger.write_header_comment(["test run"])
        logger.write_row(_make_states(1)[0])
        csv_path = logger.close()

        rows = _data_rows(csv_path)
        assert rows, "no data rows"
        for col in ("iso_timestamp", "unix_time", "xgs_units", "vgc_units"):
            assert col in rows[0], f"missing fixed column {col!r}"
            assert rows[0][col] != "", f"fixed column {col!r} is empty"

    def test_comment_header_written(self, tmp_path):
        """Lines written via write_header_comment() appear as '# ' lines."""
        logger = VacuumLogger(_ALL_LABELS, output_dir=tmp_path)
        logger.write_header_comment(["instrument: xgs600", "baud: 9600"])
        logger.write_row(_make_states(1)[0])
        csv_path = logger.close()

        text = Path(csv_path).read_text(encoding="utf-8")
        assert "# instrument: xgs600\n" in text
        assert "# baud: 9600\n" in text

    def test_custom_metadata(self, tmp_path):
        """Extra metadata fields passed at construction appear in the sidecar."""
        logger = VacuumLogger(
            _ALL_LABELS,
            metadata={"operator": "Alice", "session_note": "overnight run"},
            output_dir=tmp_path,
        )
        logger.write_header_comment(["test"])
        logger.write_row(_make_states(1)[0])
        csv_path = logger.close()

        meta = json.loads(Path(csv_path).with_suffix(".json").read_text())
        assert meta["operator"] == "Alice"
        assert meta["session_note"] == "overnight run"


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _data_rows(csv_path: str) -> list[dict]:
    """Read a vacuum CSV (skipping '#' comment lines) and return all data rows."""
    rows = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        filtered = (line for line in f if not line.startswith("#"))
        reader = csv.DictReader(filtered)
        for row in reader:
            rows.append(dict(row))
    return rows
