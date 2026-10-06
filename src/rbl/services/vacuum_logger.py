"""
vacuum_logger.py
CSV + JSON sidecar session log for vacuum pressure readings.

CONVENTIONS
-----------
Matches calibration_writer.py's conventions exactly so one log format
convention governs all instrument data in the repo:

- One CSV per session, opened on "start logging", closed on "stop".
- Logging starts automatically on the first reading (the Vacuum tab does
  this); it is not an operator action.
- File name: vacuum_YYYYMMDDTHHMMSS.csv (a caller may pass ``file_stem`` to
  fix the stem), under ``config.paths.VACUUM_DIR``:
    frozen build : dist/RBL/data/vacuum/
    development  : <repo_root>/data/vacuum/
- A name collision gets a ``_2``, ``_3`` ... suffix (log_rollover.unused_path)
  rather than overwriting. The JSON sidecar shares the chosen stem.
- flush() after every row — an overnight run that dies must leave a
  readable file.
- Column set is fixed at file-open from the channels discovered at that
  moment.  If the channel set changes mid-session (a board is added or
  the worker reconnects with a different set), the current file is closed
  and a new one opened with a _2, _3, ... suffix.
- None pressures are written as an EMPTY FIELD.  The state column carries
  "OFF" / "OVER" / "OFF_OR_OVERRANGE" etc.  The sentinel 1.10E+03 must
  never appear as a number in any column.
- A '#' comment block at the top of each file records instrument
  identities, firmware versions, baud rates and units so the file is
  self-describing months later.

WHY THIS EXISTS
---------------
One vacuum log was left running for two weeks and became a single 176 MB
file, and a name reused by a later run could silently replace an earlier
run's data. Every file name now goes through log_rollover.unused_path so no
vacuum file is ever overwritten, and the stem can be fixed by the caller (for
example ``vacuum`` inside a session folder).

HEADER FORMAT
-------------
    # vacuum_logger RBL  <iso timestamp>
    # xgs600: firmware=<ident>  units=<units>  port=<port>  baud=<baud>
    # vgc083: firmware=<ident>  units=Torr     port=<port>  baud=<baud>
    # channels: <label0>, <label1>, ...
    iso_timestamp,unix_time,xgs_units,vgc_units,<label0>,<label0>_state,...
"""
import csv
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from rbl.services.log_rollover import unused_path

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output directory — same root as calibration_writer, separate leaf
# ---------------------------------------------------------------------------

def _output_dir() -> Path:
    from rbl.config.paths import VACUUM_DIR
    return VACUUM_DIR


def _new_run_id() -> str:
    return time.strftime("vacuum_%Y%m%dT%H%M%S")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Column schema helpers
# ---------------------------------------------------------------------------

def _fixed_columns() -> list[str]:
    return ["iso_timestamp", "unix_time", "xgs_units", "vgc_units"]


def _gauge_columns(gauge_labels: list[str]) -> list[str]:
    cols = []
    for label in gauge_labels:
        cols.append(label)
        cols.append(f"{label}_state")
    return cols


def gauge_labels(state) -> list[str]:
    """Ordered list of gauge label strings extracted from a VacuumState snapshot."""
    labels = []
    for r in getattr(state, "xgs_readings", []):
        labels.append(f"xgs600:{r.channel.label}")
    for r in getattr(state, "vgc_readings", []):
        labels.append(f"vgc083:{r.channel}")
    return labels


# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------

class VacuumLogger:
    """One CSV + JSON sidecar session.

    Parameters
    ----------
    gauge_labels : ordered list of gauge label strings (the column names).
                   Typically built from the first VacuumState the tab
                   receives after "Start Logging" is clicked.
    metadata     : extra fields written into the JSON sidecar at close.
    output_dir   : override for testing (default: _output_dir()).
    file_stem    : fixed file stem; default is today's run id. A taken name
                   gets a _2, _3 ... suffix; nothing is overwritten.
    """

    def __init__(self, gauge_labels: list[str], *,
                 metadata: dict = None,
                 output_dir: Path = None,
                 file_stem: str | None = None):
        self._gauge_labels = list(gauge_labels)
        self._output_dir   = Path(output_dir) if output_dir else _output_dir()
        self._output_dir.mkdir(parents=True, exist_ok=True)

        self._csv_path = unused_path(
            self._output_dir, file_stem or _new_run_id(), ".csv")
        self._run_id   = self._csv_path.stem   # the stem actually chosen
        self._meta_path = self._csv_path.with_suffix(".json")

        self._metadata: dict = dict(metadata or {})
        self._metadata.setdefault("run_id",              self._run_id)
        self._metadata.setdefault("start_timestamp_iso", _now_iso())
        self._metadata.setdefault("gauge_labels",        gauge_labels)

        # Fixed columns + one pressure + one state column per gauge
        self._columns = _fixed_columns() + _gauge_columns(gauge_labels)

        self._file   = open(self._csv_path, "w", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._file, fieldnames=self._columns)
        # Write comment header before the CSV header row
        # (csv.DictWriter won't do this; we write raw lines first)
        self._closed = False

    def write_header_comment(self, comment_lines: list[str]):
        """Write '# ' comment lines before the CSV header.

        Must be called before the first write_row() call.
        """
        if self._closed:
            return
        for line in comment_lines:
            self._file.write(f"# {line}\n")
        self._writer.writeheader()
        self._file.flush()

    def write_row(self, state) -> bool:
        """Append one VacuumState as a CSV row.

        Returns True on success, False if the gauge label set has changed
        (caller should close this logger and open a new one).

        None pressures are written as empty fields.
        State strings are written in the corresponding _state column.
        The sentinel 1.10E+03 can never reach here as a number because
        the driver converts it to None before it reaches the snapshot.
        """
        if self._closed:
            raise RuntimeError(
                f"write_row after close() on {self._csv_path}"
            )

        # Build a flat mapping from this VacuumState's readings.
        current_labels: list[str] = []
        readings_map: dict[str, tuple] = {}   # label -> (pressure, state_str)

        for r in state.xgs_readings:
            label = f"xgs600:{r.channel.label}"
            current_labels.append(label)
            readings_map[label] = (r.pressure, r.state)

        for r in state.vgc_readings:
            label = f"vgc083:{r.channel}"
            current_labels.append(label)
            readings_map[label] = (r.pressure, r.state)

        # Guard: if the channel set has changed, signal the caller.
        if current_labels != self._gauge_labels:
            log.warning(
                "vacuum_logger: gauge label set changed "
                "(was %s, now %s) — caller should reopen logger",
                self._gauge_labels, current_labels,
            )
            return False

        row: dict[str, str] = {col: "" for col in self._columns}
        row["iso_timestamp"] = _now_iso()
        row["unix_time"]     = f"{state.timestamp:.3f}"
        row["xgs_units"]     = state.units_xgs
        row["vgc_units"]     = state.units_vgc

        for label in self._gauge_labels:
            pressure, state_str = readings_map.get(label, (None, "MISSING"))

            # Critical: None pressure -> empty field.  Never write a float
            # that originated from a sentinel or an error state.
            row[label]              = f"{pressure:.6e}" if pressure is not None else ""
            row[f"{label}_state"]   = state_str

        self._writer.writerow(row)
        self._file.flush()
        return True

    def update_metadata(self, **fields):
        self._metadata.update(fields)

    def close(self) -> str:
        """Finalise the CSV and write the JSON sidecar. Idempotent."""
        if self._closed:
            return str(self._csv_path)
        self._metadata.setdefault("end_timestamp_iso", _now_iso())
        try:
            self._file.close()
        except Exception:
            log.exception("vacuum_logger: failed closing %s", self._csv_path)
        self._closed = True
        try:
            with open(self._meta_path, "w", encoding="utf-8") as f:
                json.dump(self._metadata, f, indent=2, default=str)
        except Exception:
            log.exception("vacuum_logger: failed writing sidecar %s",
                          self._meta_path)
        return str(self._csv_path)

    @property
    def csv_path(self) -> str:
        return str(self._csv_path)
