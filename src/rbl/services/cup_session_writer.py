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
4. Per-insertion summary rows (Ticket 11):
   - One summary row per insertion written when the run closes.
   - Commanded and confirmed timestamps (measuring mechanical lag).
   - Dwell, post-settle sample count, mean current, and sample standard deviation.
   - Elapsed beam-on seconds since previous insertion (excluding cup-in-beam time).
   - Running dose chain stages (Q, fluence, dpa), each in its own column so
     the arithmetic can be reconstructed by hand from the file alone.
5. Session header:
   - Self-describing comment header carrying species, beam energy, ion charge state,
     irradiated area, displacement damage coefficient (k) with its damage depth,
     SRIM version, entry date, and cycle period and dwell in force.
   - Absent provenance fields are recorded with explicit markers (NOT_SPECIFIED),
     never as blanks or zeroes that could be misread.

TIMING AND CLOCK CONSISTENCY
----------------------------
The logging is consistent or it is useless. A transition marker's timestamp, an
insertion summary row's timestamp, and a sample row's timestamp must be the same
clock and the same format, so a reader can interleave them chronologically without
guessing. All host timestamps share the exact same format (f"{t_host:.6f}") and
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
The CSV file is flushed after every row (sample, transition, fault, marker, or summary).
An 11-hour drift run that dies at hour 11 leaves 11 hours of usable data on disk.
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
    CUP_SETTLE_WINDOW_S,
)
from rbl.config.paths import FARADAY_CUP_DIR
from rbl.hardware.cup_status import CupPosition
from rbl.hardware.dose_model import (
    InsertionCurrentStats,
    compute_dpa,
    compute_fluence,
    compute_insertion_current,
)

log = logging.getLogger(__name__)

PROVENANCE_ABSENT: str = "NOT_SPECIFIED"


@dataclass(frozen=True)
class CupRunStats:
    """Statistics for an acquisition run computed directly from logged samples."""

    run_id: int | None
    duration_s: float
    total_samples: int
    valid_samples: int
    over_range_samples: int
    average_current_a: float | None
    post_settle_samples: int = 0
    post_settle_mean_a: float | None = None
    post_settle_std_a: float | None = None
    excluded_settle_samples: int = 0


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
    "commanded_timestamp",
    "confirmed_timestamp",
    "dwell",
    "sample_count",
    "mean_current_a",
    "std_current_a",
    "beam_on_seconds",
    "charge",
    "charge_state",
    "area",
    "k",
    "fluence",
    "dpa",
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


def _format_header_val(v: Any, fmt: str | None = None) -> str:
    """Format session header value, substituting PROVENANCE_ABSENT if blank, zero, or None."""
    if v is None:
        return PROVENANCE_ABSENT
    if isinstance(v, (int, float)):
        if v <= 0 or math.isnan(v) or math.isinf(v):
            return PROVENANCE_ABSENT
        if fmt is not None:
            return f"{v:{fmt}}"
        return f"{v}"
    s = str(v).strip()
    return s if s else PROVENANCE_ABSENT


class CupSessionWriter:
    """Writes Faraday cup acquisition samples, lifecycle markers, and dose summary rows.

    Parameters
    ----------
    session_id     : Optional unique session identifier string.
    output_dir     : Directory to store CSV and JSON files (default: FARADAY_CUP_DIR).
    metadata       : Extra metadata fields for the JSON sidecar.
    species        : Ion species name (e.g. 'Fe56').
    energy         : Beam energy (e.g. '5.0 MeV').
    charge_state   : Ion charge state q (positive integer >= 1).
    area_cm2       : Irradiated sample area in cm².
    k              : Displacement damage coefficient in dpa / (ions/cm²).
    k_depth        : Damage depth for SRIM calculation in nanometers.
    srim_version   : SRIM calculation version string.
    entry_date     : Date coefficient was derived or entered (YYYY-MM-DD).
    cycle_period_s : Sampling cycle period in seconds.
    cycle_dwell_s  : Sampling cycle dwell in seconds.
    """

    def __init__(
        self,
        session_id: str | None = None,
        output_dir: Path | str | None = None,
        metadata: dict[str, Any] | None = None,
        species: str | None = None,
        energy: str | float | None = None,
        charge_state: int | None = None,
        area_cm2: float | None = None,
        k: float | None = None,
        k_depth: str | float | None = None,
        srim_version: str | None = None,
        entry_date: str | None = None,
        cycle_period_s: float | None = None,
        cycle_dwell_s: float | None = None,
    ) -> None:
        self.session_id: str = session_id or _new_session_id()
        self.output_dir: Path = Path(output_dir) if output_dir is not None else FARADAY_CUP_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self._csv_path: Path = self.output_dir / f"{self.session_id}.csv"
        self._meta_path: Path = self.output_dir / f"{self.session_id}.json"

        self._species: str | None = species
        self._energy: str | float | None = energy
        self._charge_state: int | None = charge_state
        self._area_cm2: float | None = area_cm2
        self._k: float | None = k
        self._k_depth: str | float | None = k_depth
        self._srim_version: str | None = srim_version
        self._entry_date: str | None = entry_date
        self._cycle_period_s: float | None = cycle_period_s
        self._cycle_dwell_s: float | None = cycle_dwell_s

        self._metadata: dict[str, Any] = dict(metadata or {})
        self._metadata.setdefault("session_id", self.session_id)
        self._metadata.setdefault("start_timestamp_iso", _now_iso())
        self._metadata.setdefault("arm_threshold_a", CUP_ARM_THRESHOLD_A)
        self._metadata.setdefault("release_threshold_a", CUP_RELEASE_THRESHOLD_A)
        self._metadata.setdefault("arm_debounce_s", CUP_ARM_DEBOUNCE_S)
        self._metadata.setdefault("release_interval_s", CUP_RELEASE_INTERVAL_S)
        self._metadata.setdefault("species", self._species)
        self._metadata.setdefault("energy", self._energy)
        self._metadata.setdefault("charge_state", self._charge_state)
        self._metadata.setdefault("area_cm2", self._area_cm2)
        self._metadata.setdefault("k", self._k)
        self._metadata.setdefault("k_depth", self._k_depth)
        self._metadata.setdefault("srim_version", self._srim_version)
        self._metadata.setdefault("entry_date", self._entry_date)
        self._metadata.setdefault("cycle_period_s", self._cycle_period_s)
        self._metadata.setdefault("cycle_dwell_s", self._cycle_dwell_s)

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

        # Active run statistics & sample tracking (computed strictly from logged samples)
        self._active_run_id: int | None = None
        self._active_run_t_start: float = 0.0
        self._active_run_t_last: float = 0.0
        self._active_run_total_samples: int = 0
        self._active_run_valid_samples: int = 0
        self._active_run_over_range_samples: int = 0
        self._active_run_current_sum: float = 0.0
        self._active_run_samples: list[tuple[float, float | None]] = []
        self._last_run_stats: CupRunStats | None = None
        self._last_insertion_stats: InsertionCurrentStats | None = None

    def _write_header_comments(self) -> None:
        """Write self-describing comments at the top of the CSV file."""
        self._file.write(f"# cup_session_writer RBL {_now_iso()}\n")
        self._file.write(
            f"# thresholds: arm={CUP_ARM_THRESHOLD_A:.3e} A  "
            f"release={CUP_RELEASE_THRESHOLD_A:.3e} A  "
            f"arm_debounce={CUP_ARM_DEBOUNCE_S:.1f} s  "
            f"release_interval={CUP_RELEASE_INTERVAL_S:.1f} s\n"
        )
        species_str = _format_header_val(self._species)
        energy_str = _format_header_val(self._energy)
        cs_str = _format_header_val(self._charge_state)
        area_str = _format_header_val(self._area_cm2, ".4f")
        k_str = _format_header_val(self._k, ".4e")
        depth_str = _format_header_val(self._k_depth)
        srim_str = _format_header_val(self._srim_version)
        entry_date_str = _format_header_val(self._entry_date)
        period_str = _format_header_val(self._cycle_period_s, ".1f")
        dwell_str = _format_header_val(self._cycle_dwell_s, ".1f")

        self._file.write(
            f"# session_header: species={species_str}  energy={energy_str}  "
            f"charge_state={cs_str}  area={area_str}  "
            f"k={k_str}  k_depth={depth_str}  "
            f"srim_version={srim_str}  entry_date={entry_date_str}  "
            f"cycle_period={period_str}  cycle_dwell={dwell_str}\n"
        )
        self._file.write(
            "# record types: sample, run_opened, run_closed, idle_heartbeat, "
            "connected, disconnected, position_transition, "
            "fault_move_not_confirmed, fault_controller_not_in_auto, "
            "fault_impossible_status, fault_disagreement, "
            "cycle_insertion_skipped, insertion_summary\n"
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
                self._active_run_samples.append((t_host, None))
            elif current is not None and not math.isnan(current) and not math.isinf(current):
                self._active_run_valid_samples += 1
                self._active_run_current_sum += current
                self._active_run_samples.append((t_host, current))
            else:
                self._active_run_samples.append((t_host, None))

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
        self._active_run_samples = []
        self._last_run_stats = None
        self._last_insertion_stats = None

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
            insertion_stats = compute_insertion_current(
                self._active_run_samples,
                start_t=self._active_run_t_start,
                settle_window_s=CUP_SETTLE_WINDOW_S,
            )
            self._last_insertion_stats = insertion_stats
            self._last_run_stats = CupRunStats(
                run_id=run_id,
                duration_s=dur,
                total_samples=self._active_run_total_samples,
                valid_samples=self._active_run_valid_samples,
                over_range_samples=self._active_run_over_range_samples,
                average_current_a=avg,
                post_settle_samples=insertion_stats.sample_count,
                post_settle_mean_a=insertion_stats.mean_a,
                post_settle_std_a=insertion_stats.std_a,
                excluded_settle_samples=insertion_stats.excluded_count,
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

    def write_cycle_insertion_skipped(
        self,
        t_host: float,
        insertion_due_t: float,
        details: str = "",
    ) -> None:
        """Write a marker when a scheduled cycle insertion is skipped.

        A skip happens when a period boundary falls due while a manual run is
        open. The insertion is not queued — the scheduler advances to the next
        period boundary. This row records the skipped insertion time so a
        reviewer can tell the gap apart from instrument downtime.
        """
        desc = f"insertion_due_t={insertion_due_t:.3f} s skipped; manual run was open"
        det = f"{desc}; {details}" if details else desc
        row = {
            "record_type": "cycle_insertion_skipped",
            "iso_timestamp": _now_iso(),
            "host_timestamp": f"{t_host:.6f}",
            "inst_timestamp": "",
            "current_a": "",
            "status_word": "",
            "over_range": "",
            "run_id": str(self._active_run_id) if self._active_run_id is not None else "",
            "details": det,
        }
        self._write_row(row)

    def write_insertion_summary(
        self,
        run_id: int,
        commanded_timestamp: float | None,
        confirmed_timestamp: float,
        dwell: float,
        sample_count: int,
        mean_current_a: float,
        std_current_a: float,
        beam_on_seconds: float,
        charge: float,
        charge_state: int | None = None,
        area: float | None = None,
        k: float | None = None,
        fluence: float | None = None,
        dpa: float | None = None,
        t_host: float | None = None,
        details: str = "",
    ) -> None:
        """Write a per-insertion summary row to the CSV archive and flush immediately.

        WHY THIS EXISTS (ADR 0003 Decision 7, Ticket 11)
        ------------------------------------------------
        An irradiation leaves an archive of periodic sampling insertions. One summary
        row per insertion records the post-settle mean current, sample standard deviation,
        mechanical lag (commanded vs confirmed timestamps), dwell, preceding beam-on
        interval (between insertions), and the running dose chain (Q, fluence, dpa).

        Each stage of the dose chain is recorded in its own column so the arithmetic
        can be recomputed and traced by hand from the file alone.
        """
        cs = charge_state if charge_state is not None else self._charge_state
        a = area if area is not None else self._area_cm2
        k_val = k if k is not None else self._k

        calc_fluence = fluence
        if calc_fluence is None:
            calc_fluence = (
                compute_fluence(charge, cs, a)
                if (cs is not None and cs > 0 and a is not None and a > 0.0)
                else 0.0
            )

        calc_dpa = dpa
        if calc_dpa is None:
            calc_dpa = (
                compute_dpa(calc_fluence, k_val)
                if (k_val is not None and k_val > 0.0)
                else 0.0
            )

        cmd_ts_str = (
            f"{commanded_timestamp:.6f}"
            if (commanded_timestamp is not None and not math.isnan(commanded_timestamp))
            else ""
        )
        conf_ts_str = f"{confirmed_timestamp:.6f}"
        host_ts_str = f"{t_host:.6f}" if t_host is not None else conf_ts_str

        row = {
            "record_type": "insertion_summary",
            "iso_timestamp": _now_iso(),
            "host_timestamp": host_ts_str,
            "inst_timestamp": "",
            "current_a": _safe_float(mean_current_a, ".8e"),
            "status_word": "",
            "over_range": "",
            "run_id": str(run_id),
            "details": details,
            "position": "IN",
            "commanded_timestamp": cmd_ts_str,
            "confirmed_timestamp": conf_ts_str,
            "dwell": f"{dwell:.3f}",
            "sample_count": str(sample_count),
            "mean_current_a": _safe_float(mean_current_a, ".8e"),
            "std_current_a": _safe_float(std_current_a, ".8e"),
            "beam_on_seconds": f"{beam_on_seconds:.3f}",
            "charge": _safe_float(charge, ".8e"),
            "charge_state": str(cs) if (cs is not None and cs > 0) else "",
            "area": _safe_float(a, ".6f") if (a is not None and a > 0.0) else "",
            "k": _safe_float(k_val, ".8e") if (k_val is not None and k_val > 0.0) else "",
            "fluence": _safe_float(calc_fluence, ".8e"),
            "dpa": _safe_float(calc_dpa, ".8e"),
        }
        self._write_row(row)

    # ── Metadata and Lifecycle ────────────────────────────────────────────────

    def update_session_parameters(
        self,
        species: str | None = None,
        energy: str | float | None = None,
        charge_state: int | None = None,
        area_cm2: float | None = None,
        k: float | None = None,
        k_depth: str | float | None = None,
        srim_version: str | None = None,
        entry_date: str | None = None,
        cycle_period_s: float | None = None,
        cycle_dwell_s: float | None = None,
    ) -> None:
        """Update session parameters and rewrite header comments if no data rows written yet."""
        if species is not None:
            self._species = species
        if energy is not None:
            self._energy = energy
        if charge_state is not None:
            self._charge_state = charge_state
        if area_cm2 is not None:
            self._area_cm2 = area_cm2
        if k is not None:
            self._k = k
        if k_depth is not None:
            self._k_depth = k_depth
        if srim_version is not None:
            self._srim_version = srim_version
        if entry_date is not None:
            self._entry_date = entry_date
        if cycle_period_s is not None:
            self._cycle_period_s = cycle_period_s
        if cycle_dwell_s is not None:
            self._cycle_dwell_s = cycle_dwell_s

        self.update_metadata(
            species=self._species,
            energy=self._energy,
            charge_state=self._charge_state,
            area_cm2=self._area_cm2,
            k=self._k,
            k_depth=self._k_depth,
            srim_version=self._srim_version,
            entry_date=self._entry_date,
            cycle_period_s=self._cycle_period_s,
            cycle_dwell_s=self._cycle_dwell_s,
        )

        if not self._closed and self._row_count == 0:
            self._file.seek(0)
            self._file.truncate(0)
            self._write_header_comments()
            self._writer.writeheader()
            self._file.flush()

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
    def species(self) -> str | None:
        return self._species

    @property
    def energy(self) -> str | float | None:
        return self._energy

    @property
    def charge_state(self) -> int | None:
        return self._charge_state

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

    @property
    def last_insertion_stats(self) -> InsertionCurrentStats:
        """Return post-settle insertion current statistics of the last completed run."""
        if self._last_insertion_stats is not None:
            return self._last_insertion_stats
        return InsertionCurrentStats(
            mean_a=0.0,
            std_a=0.0,
            sample_count=0,
            excluded_count=0,
        )

    @property
    def active_run_insertion_stats(self) -> InsertionCurrentStats:
        """Return post-settle insertion current statistics for the current run (or last)."""
        if self._active_run_id is not None:
            return compute_insertion_current(
                self._active_run_samples,
                start_t=self._active_run_t_start,
                settle_window_s=CUP_SETTLE_WINDOW_S,
            )
        return self.last_insertion_stats
