"""
cup_session_writer.py
CSV + JSON metadata sidecar session log for Faraday cup current acquisition runs.

WHY THIS EXISTS
---------------
ADR 0002 records that the Faraday cup is inserted manually and carries no position
sensor. The application infers an insertion from measured cup current, starting an
acquisition run when the cup enters the beam and closing it when the cup is withdrawn.

This session writer logs every sample inside an acquisition run to disk, allowing an
irradiation's delivered dose to be reconstructed accurately after the shift from the
application's own records rather than handwritten notes.

ONE FILE PER APPLICATION SESSION
---------------------------------
Unlike per-insertion logs, there is exactly one session file per application run.
Rows represent samples inside active runs. Idle periods between insertions are not
logged as sample rows to avoid gigabytes of baseline noise.

MARKERS & UNAMBIGUOUS RECORD
----------------------------
A gap in the record must never be ambiguous between three different causes:
1. The cup was out and the application was watching (recorded via periodic idle heartbeats).
2. The application was not running (silence with no heartbeats).
3. The instrument was disconnected (recorded via explicit disconnect/connect markers).

Run transitions (run-opened with active thresholds, and run-closed with reason and sample count)
are recorded as marker rows.

CRASH RESILIENCE
----------------
The CSV file is flushed after every row (sample or marker). An 11-hour drift run that
dies at hour 11 leaves 11 hours of usable data on disk.
"""
from __future__ import annotations

import csv
import json
import logging
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rbl.config.cup_config import (
    CUP_ARM_DEBOUNCE_S,
    CUP_ARM_THRESHOLD_A,
    CUP_RELEASE_INTERVAL_S,
    CUP_RELEASE_THRESHOLD_A,
)
from rbl.config.paths import FARADAY_CUP_DIR

log = logging.getLogger(__name__)

# Column schema for the Faraday cup session CSV
CSV_COLUMNS: list[str] = [
    "record_type",
    "iso_timestamp",
    "host_timestamp",
    "inst_timestamp",
    "current_a",
    "status_word",
    "over_range",
    "run_id",
    "details",
]


def _new_session_id() -> str:
    return time.strftime("cup_%Y%m%dT%H%M%S")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(v: float | None, fmt: str = ".8e") -> str:
    if v is None:
        return ""
    if math.isnan(v) or math.isinf(v):
        return ""
    return f"{v:{fmt}}"


class CupSessionWriter:
    """Writes Faraday cup acquisition samples and lifecycle markers to CSV + JSON sidecar.

    Parameters
    ----------
    session_id : Optional unique session identifier string.
    output_dir : Directory to store CSV and JSON files (default: FARADAY_CUP_DIR).
    metadata   : Extra metadata fields for the JSON sidecar.
    """

    def __init__(
        self,
        session_id: str | None = None,
        output_dir: Path | str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.session_id: str = session_id or _new_session_id()
        self.output_dir: Path = Path(output_dir) if output_dir is not None else FARADAY_CUP_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self._csv_path: Path = self.output_dir / f"{self.session_id}.csv"
        self._meta_path: Path = self.output_dir / f"{self.session_id}.json"

        self._metadata: dict[str, Any] = dict(metadata or {})
        self._metadata.setdefault("session_id", self.session_id)
        self._metadata.setdefault("start_timestamp_iso", _now_iso())
        self._metadata.setdefault("arm_threshold_a", CUP_ARM_THRESHOLD_A)
        self._metadata.setdefault("release_threshold_a", CUP_RELEASE_THRESHOLD_A)
        self._metadata.setdefault("arm_debounce_s", CUP_ARM_DEBOUNCE_S)
        self._metadata.setdefault("release_interval_s", CUP_RELEASE_INTERVAL_S)

        self._file = open(self._csv_path, "w", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._file, fieldnames=CSV_COLUMNS)

        # Write self-describing comment header
        self._write_header_comments()
        self._writer.writeheader()
        self._file.flush()

        self._closed: bool = False
        self._row_count: int = 0
        self._sample_count: int = 0
        self._runs_recorded: set[int] = set()

    def _write_header_comments(self) -> None:
        """Write self-describing comments at the top of the CSV file."""
        self._file.write(f"# cup_session_writer RBL {_now_iso()}\n")
        self._file.write(
            f"# thresholds: arm={CUP_ARM_THRESHOLD_A:.3e} A  "
            f"release={CUP_RELEASE_THRESHOLD_A:.3e} A  "
            f"arm_debounce={CUP_ARM_DEBOUNCE_S:.1f} s  "
            f"release_interval={CUP_RELEASE_INTERVAL_S:.1f} s\n"
        )
        self._file.write("# columns: " + ", ".join(CSV_COLUMNS) + "\n")

    def _write_row(self, row_dict: dict[str, str]) -> None:
        """Write a dictionary row and immediately flush to disk."""
        if self._closed:
            raise RuntimeError(f"write_row after close() on {self._csv_path}")
        self._writer.writerow({col: row_dict.get(col, "") for col in CSV_COLUMNS})
        self._file.flush()
        self._row_count += 1

    # ── Public Writers: Samples & Markers ─────────────────────────────────────

    def write_sample(
        self,
        t_host: float,
        t_inst: float | None,
        current: float | None,
        status_word: int | None,
        over_range: bool,
        run_id: int,
        details: str = "",
    ) -> None:
        """Append one acquisition sample row during an active run."""
        row = {
            "record_type": "sample",
            "iso_timestamp": _now_iso(),
            "host_timestamp": f"{t_host:.6f}",
            "inst_timestamp": _safe_float(t_inst, ".6f"),
            "current_a": _safe_float(current, ".8e"),
            "status_word": f"0x{status_word:08X}" if status_word is not None else "",
            "over_range": "True" if over_range else "False",
            "run_id": str(run_id),
            "details": details,
        }
        self._write_row(row)
        self._sample_count += 1
        self._runs_recorded.add(run_id)

    def write_run_opened(
        self,
        t_host: float,
        run_id: int,
        arm_threshold: float,
        release_threshold: float,
        forced: bool = False,
        t_inst: float | None = None,
        details: str = "",
    ) -> None:
        """Write a marker recording that an acquisition run began and its active thresholds."""
        threshold_info = (
            f"arm={arm_threshold:.3e} A, release={release_threshold:.3e} A, forced={forced}"
        )
        det = f"{threshold_info}; {details}" if details else threshold_info
        row = {
            "record_type": "run_opened",
            "iso_timestamp": _now_iso(),
            "host_timestamp": f"{t_host:.6f}",
            "inst_timestamp": _safe_float(t_inst, ".6f"),
            "current_a": "",
            "status_word": "",
            "over_range": "",
            "run_id": str(run_id),
            "details": det,
        }
        self._write_row(row)
        self._runs_recorded.add(run_id)

    def write_run_closed(
        self,
        t_host: float,
        run_id: int,
        reason: str,
        sample_count: int = 0,
        duration_s: float = 0.0,
        t_inst: float | None = None,
        details: str = "",
    ) -> None:
        """Write a marker recording that an acquisition run ended."""
        close_info = f"reason={reason}"
        if sample_count > 0:
            close_info += f", samples={sample_count}"
        if duration_s > 0.0:
            close_info += f", duration_s={duration_s:.3f}"
        det = f"{close_info}; {details}" if details else close_info
        row = {
            "record_type": "run_closed",
            "iso_timestamp": _now_iso(),
            "host_timestamp": f"{t_host:.6f}",
            "inst_timestamp": _safe_float(t_inst, ".6f"),
            "current_a": "",
            "status_word": "",
            "over_range": "",
            "run_id": str(run_id),
            "details": det,
        }
        self._write_row(row)

    def write_idle_heartbeat(
        self,
        t_host: float,
        t_inst: float | None = None,
        details: str = "idle_polling",
    ) -> None:
        """Write a periodic idle heartbeat marker indicating the app is watching out of beam."""
        row = {
            "record_type": "idle_heartbeat",
            "iso_timestamp": _now_iso(),
            "host_timestamp": f"{t_host:.6f}",
            "inst_timestamp": _safe_float(t_inst, ".6f"),
            "current_a": "",
            "status_word": "",
            "over_range": "",
            "run_id": "",
            "details": details,
        }
        self._write_row(row)

    def write_connected(
        self,
        t_host: float,
        ident: str = "",
        resource: str = "",
        details: str = "",
    ) -> None:
        """Write an instrument connection / reconnection marker."""
        info = []
        if ident:
            info.append(f"ident={ident}")
        if resource:
            info.append(f"resource={resource}")
        if details:
            info.append(details)
        row = {
            "record_type": "connected",
            "iso_timestamp": _now_iso(),
            "host_timestamp": f"{t_host:.6f}",
            "inst_timestamp": "",
            "current_a": "",
            "status_word": "",
            "over_range": "",
            "run_id": "",
            "details": "; ".join(info) if info else "connected",
        }
        self._write_row(row)

    def write_disconnected(
        self,
        t_host: float,
        reason: str = "",
        details: str = "",
    ) -> None:
        """Write an instrument disconnection marker."""
        info = []
        if reason:
            info.append(f"reason={reason}")
        if details:
            info.append(details)
        row = {
            "record_type": "disconnected",
            "iso_timestamp": _now_iso(),
            "host_timestamp": f"{t_host:.6f}",
            "inst_timestamp": "",
            "current_a": "",
            "status_word": "",
            "over_range": "",
            "run_id": "",
            "details": "; ".join(info) if info else "disconnected",
        }
        self._write_row(row)

    # ── Metadata and Lifecycle ────────────────────────────────────────────────

    def update_metadata(self, **fields: Any) -> None:
        """Merge additional metadata into the session sidecar."""
        self._metadata.update(fields)

    def close(self) -> str:
        """Finalise the CSV and write the JSON metadata sidecar. Idempotent."""
        if self._closed:
            return str(self._csv_path)

        self._metadata.setdefault("end_timestamp_iso", _now_iso())
        self._metadata["total_rows"] = self._row_count
        self._metadata["total_samples"] = self._sample_count
        self._metadata["total_runs"] = len(self._runs_recorded)

        try:
            self._file.close()
        except Exception:
            log.exception("CupSessionWriter: failed closing %s", self._csv_path)
        self._closed = True

        try:
            with open(self._meta_path, "w", encoding="utf-8") as f:
                json.dump(self._metadata, f, indent=2, default=str)
        except Exception:
            log.exception("CupSessionWriter: failed writing sidecar %s", self._meta_path)

        return str(self._csv_path)

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def csv_path(self) -> str:
        return str(self._csv_path)

    @property
    def meta_path(self) -> str:
        return str(self._meta_path)

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def row_count(self) -> int:
        return self._row_count

    @property
    def sample_count(self) -> int:
        return self._sample_count

    @property
    def run_count(self) -> int:
        return len(self._runs_recorded)
