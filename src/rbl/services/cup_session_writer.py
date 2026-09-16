"""
cup_session_writer.py
CSV + JSON metadata sidecar session log for Faraday cup current acquisition runs.

WHY THIS EXISTS
---------------
ADR 0002 recorded that the Faraday cup was historically inserted manually and carried
no position sensor. The application inferred an insertion from measured cup current,
starting an acquisition run when the cup enters the beam and closing it when withdrawn.

ADR 0003 introduces remote actuation via LabJack T7 digital outputs and confirmed
position feedback via the controller's isolated status contacts (FIO2 = IN, FIO3 = OUT,
FIO4 = AUTO). The session file now logs:
1. Active acquisition samples during runs.
2. Confirmed position transitions (IN and OUT).
3. Actuation and authority faults:
   - Move commanded but not confirmed within timeout.
   - Controller not in AUTO (LOCAL mode).
   - Impossible status combination (both contacts asserted / indeterminate).
   - Position-versus-current disagreement (carrying both readings).

TIMING AND CLOCK CONSISTENCY
----------------------------
The logging is consistent or it is useless. A transition marker's timestamp and a
sample row's timestamp must be the same clock and the same format, so a reader can
interleave them chronologically without guessing. Transition markers, fault rows,
and sample rows share the exact same host_timestamp format (f"{t_host:.6f}") and
UTC ISO timestamp.

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
The CSV file is flushed after every row (sample, transition, fault, or marker). An 11-hour
drift run that dies at hour 11 leaves 11 hours of usable data on disk.
"""
from __future__ import annotations

import csv
import json
import logging
import math
import time
from dataclasses import dataclass
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
from rbl.hardware.cup_status import CupPosition

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CupRunStats:
    """Statistics for an acquisition run computed directly from logged samples."""

    run_id: int | None
    duration_s: float
    total_samples: int
    valid_samples: int
    over_range_samples: int
    average_current_a: float | None


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
    "position",
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

        # Active run statistics (computed strictly from logged samples)
        self._active_run_id: int | None = None
        self._active_run_t_start: float = 0.0
        self._active_run_t_last: float = 0.0
        self._active_run_total_samples: int = 0
        self._active_run_valid_samples: int = 0
        self._active_run_over_range_samples: int = 0
        self._active_run_current_sum: float = 0.0
        self._last_run_stats: CupRunStats | None = None

    def _write_header_comments(self) -> None:
        """Write self-describing comments at the top of the CSV file."""
        self._file.write(f"# cup_session_writer RBL {_now_iso()}\n")
        self._file.write(
            f"# thresholds: arm={CUP_ARM_THRESHOLD_A:.3e} A  "
            f"release={CUP_RELEASE_THRESHOLD_A:.3e} A  "
            f"arm_debounce={CUP_ARM_DEBOUNCE_S:.1f} s  "
            f"release_interval={CUP_RELEASE_INTERVAL_S:.1f} s\n"
        )
        self._file.write(
            "# record types: sample, run_opened, run_closed, idle_heartbeat, "
            "connected, disconnected, position_transition, "
            "fault_move_not_confirmed, fault_controller_not_in_auto, "
            "fault_impossible_status, fault_disagreement\n"
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

        # Update active run sample accumulation
        if self._active_run_id is not None and run_id == self._active_run_id:
            self._active_run_total_samples += 1
            self._active_run_t_last = t_host
            if over_range:
                self._active_run_over_range_samples += 1
            elif current is not None and not math.isnan(current) and not math.isinf(current):
                self._active_run_valid_samples += 1
                self._active_run_current_sum += current

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

        # Initialize active run tracking
        self._active_run_id = run_id
        self._active_run_t_start = t_host
        self._active_run_t_last = t_host
        self._active_run_total_samples = 0
        self._active_run_valid_samples = 0
        self._active_run_over_range_samples = 0
        self._active_run_current_sum = 0.0
        self._last_run_stats = None

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
        if self._active_run_id is not None and self._active_run_id == run_id:
            dur = max(0.0, t_host - self._active_run_t_start) if duration_s <= 0.0 else duration_s
            cnt = self._active_run_total_samples if sample_count <= 0 else sample_count
            avg = (
                (self._active_run_current_sum / self._active_run_valid_samples)
                if self._active_run_valid_samples > 0
                else None
            )
            self._last_run_stats = CupRunStats(
                run_id=run_id,
                duration_s=dur,
                total_samples=self._active_run_total_samples,
                valid_samples=self._active_run_valid_samples,
                over_range_samples=self._active_run_over_range_samples,
                average_current_a=avg,
            )
            sample_count = cnt
            duration_s = dur
            self._active_run_id = None

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

    def write_position_transition(
        self,
        t_host: float,
        position: CupPosition | str,
        details: str = "",
        t_inst: float | None = None,
    ) -> None:
        """Write a confirmed position transition marker (IN or OUT)."""
        pos_str = position.value if hasattr(position, "value") else str(position)
        row = {
            "record_type": "position_transition",
            "iso_timestamp": _now_iso(),
            "host_timestamp": f"{t_host:.6f}",
            "inst_timestamp": _safe_float(t_inst, ".6f"),
            "current_a": "",
            "status_word": "",
            "over_range": "",
            "run_id": str(self._active_run_id) if self._active_run_id is not None else "",
            "details": details or f"confirmed_{pos_str.lower()}",
            "position": pos_str,
        }
        self._write_row(row)

    def write_fault_move_not_confirmed(
        self,
        t_host: float,
        commanded: CupPosition | str,
        timeout_s: float,
        details: str = "",
        t_inst: float | None = None,
    ) -> None:
        """Write a fault marker for a commanded move that did not confirm within timeout."""
        cmd_str = commanded.value if hasattr(commanded, "value") else str(commanded)
        desc = f"command={cmd_str} not confirmed within {timeout_s:.1f} s"
        det = f"{desc}; {details}" if details else desc
        row = {
            "record_type": "fault_move_not_confirmed",
            "iso_timestamp": _now_iso(),
            "host_timestamp": f"{t_host:.6f}",
            "inst_timestamp": _safe_float(t_inst, ".6f"),
            "current_a": "",
            "status_word": "",
            "over_range": "",
            "run_id": str(self._active_run_id) if self._active_run_id is not None else "",
            "details": det,
            "position": cmd_str,
        }
        self._write_row(row)

    def write_fault_controller_not_in_auto(
        self,
        t_host: float,
        details: str = "controller in LOCAL mode; remote commands ignored",
        t_inst: float | None = None,
    ) -> None:
        """Write a fault marker when the controller is not in AUTO mode."""
        row = {
            "record_type": "fault_controller_not_in_auto",
            "iso_timestamp": _now_iso(),
            "host_timestamp": f"{t_host:.6f}",
            "inst_timestamp": _safe_float(t_inst, ".6f"),
            "current_a": "",
            "status_word": "",
            "over_range": "",
            "run_id": str(self._active_run_id) if self._active_run_id is not None else "",
            "details": details,
            "position": "",
        }
        self._write_row(row)

    def write_fault_impossible_status(
        self,
        t_host: float,
        raw_status: int | None = None,
        details: str = "both IN and OUT contacts asserted",
        t_inst: float | None = None,
    ) -> None:
        """Write a fault marker for an impossible contact status (indeterminate)."""
        row = {
            "record_type": "fault_impossible_status",
            "iso_timestamp": _now_iso(),
            "host_timestamp": f"{t_host:.6f}",
            "inst_timestamp": _safe_float(t_inst, ".6f"),
            "current_a": "",
            "status_word": f"0x{raw_status:08X}" if raw_status is not None else "",
            "over_range": "",
            "run_id": str(self._active_run_id) if self._active_run_id is not None else "",
            "details": details,
            "position": "INDETERMINATE",
        }
        self._write_row(row)

    def write_fault_disagreement(
        self,
        t_host: float,
        position: CupPosition | str,
        current: float | None,
        details: str = "",
        t_inst: float | None = None,
    ) -> None:
        """Write a fault marker when confirmed position and current inference disagree."""
        pos_str = (
            position.value
            if hasattr(position, "value")
            else str(position)
            if position is not None
            else ""
        )
        cur_str = _safe_float(current, ".8e")
        desc = (
            f"position={pos_str} disagrees with current={cur_str} A"
            if (pos_str and cur_str)
            else "position-current disagreement"
        )
        det = f"{desc}; {details}" if details else desc
        row = {
            "record_type": "fault_disagreement",
            "iso_timestamp": _now_iso(),
            "host_timestamp": f"{t_host:.6f}",
            "inst_timestamp": _safe_float(t_inst, ".6f"),
            "current_a": cur_str,
            "status_word": "",
            "over_range": "",
            "run_id": str(self._active_run_id) if self._active_run_id is not None else "",
            "details": det,
            "position": pos_str,
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

    @property
    def active_run_stats(self) -> CupRunStats:
        """Return running statistics for the active acquisition run (or last completed run)."""
        if self._active_run_id is not None:
            dur = max(0.0, self._active_run_t_last - self._active_run_t_start)
            avg = (
                (self._active_run_current_sum / self._active_run_valid_samples)
                if self._active_run_valid_samples > 0
                else None
            )
            return CupRunStats(
                run_id=self._active_run_id,
                duration_s=dur,
                total_samples=self._active_run_total_samples,
                valid_samples=self._active_run_valid_samples,
                over_range_samples=self._active_run_over_range_samples,
                average_current_a=avg,
            )
        if self._last_run_stats is not None:
            return self._last_run_stats
        return CupRunStats(
            run_id=None,
            duration_s=0.0,
            total_samples=0,
            valid_samples=0,
            over_range_samples=0,
            average_current_a=None,
        )
