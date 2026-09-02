"""
profile_logger.py
CSV + JSON sidecar session log for scope waveform and FWHM data.

Follows the same conventions as vacuum_logger.py and calibration_writer.py:
- One CSV per session, opened on "Start Logging", closed on "Stop".
- File name: profile_YYYYMMDD_HHMMSS.csv under ~/Desktop/RBL_log/data/scope/
- flush() after every row.
- JSON sidecar written at close() with session metadata.
- A separate .waveform.csv holds the raw voltage samples for each acquisition
  (one column per shot, so the waveform can be replayed or re-analysed).

CSV COLUMNS (stats file)
------------------------
    iso_timestamp, unix_time, channel,
    fwhm_seconds, fwhm_source, fwhm_x_seconds, fwhm_y_seconds,
    xy_ratio, mean_fwhm_seconds, fwhm_spread,
    fit_r_squared, signal_to_noise,
    n_peaks, n_resolved,
    smooth_window, envelope_samples,
    xincr, points, transfer_seconds,
    scope_pwidth_seconds,
    bpm_calibration, mm_per_second,
    fwhm_mm, fwhm_x_mm, fwhm_y_mm,
    <level>_<axis>_seconds / _mm  for every level BELOW half maximum,
    tail_ratio_x, tail_ratio_y,
    peak_details_json

THE WIDTH LADDER COLUMNS ARE GENERATED, NOT TYPED
-------------------------------------------------
`SCOPE_WIDTH_LEVELS` decides which levels are measured, so the header is
built from it rather than hard-coded: changing the levels in config changes
the CSV in step, and there is no way for a column called `fwtm_x_seconds` to
end up holding a width measured at some other level.  Half maximum is
excluded because it already has its own columns above.

`tail_ratio_*` is FWTM/FWHM as MEASURED, blank when either level was not
measurable.  A Gaussian gives 1.8226; nothing is written there from a fit,
because a fit's ratio is that constant whatever the beam is doing.

MILLIMETRES ARE AN ADDITION, NEVER A REPLACEMENT
------------------------------------------------
Every mm column sits BESIDE its seconds column, and the seconds column is
always written.  A calibration is a divide by one measured number, and that
number can later turn out to have been taken with the wrong BPM selected or
the wrong fiducial peaks picked.  When that happens the logged seconds are
still good and the file can simply be rescaled; had the log stored only
millimetres, the run would be gone.  `mm_per_second` is written on every row
for the same reason - so a file can be rescaled without anyone having to
remember what the calibration was that afternoon.
"""
import csv
import json
import logging
import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from rbl.config.paths import SCOPE_DIR

log = logging.getLogger(__name__)


def _output_dir() -> Path:
    return SCOPE_DIR


def _new_run_id() -> str:
    return time.strftime("profile_%Y%m%dT%H%M%S")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return ""
    return str(v)


def _slug(label: str) -> str:
    """A level's name as a CSV-safe column stem: "FW1/e²" -> "fw1_e2"."""
    out = re.sub(r"[^0-9a-zA-Z]+", "_", label.replace("²", "2")).strip("_")
    return out.lower()


def _level_columns() -> list:
    """Width-ladder columns, in config order, half maximum excluded."""
    try:
        from rbl.config.scope_config import SCOPE_AXIS_LABELS, SCOPE_WIDTH_LEVELS
        from rbl.hardware.profile_fwhm import level_label
    except Exception:            # pragma: no cover - config always imports
        return []
    cols = []
    for level in SCOPE_WIDTH_LEVELS:
        label = level_label(level)
        if label == "FWHM":
            continue
        for axis in SCOPE_AXIS_LABELS[:2]:
            stem = f"{_slug(label)}_{axis.lower()}"
            cols += [f"{stem}_seconds", f"{stem}_mm"]
    return cols


LEVEL_COLUMNS = _level_columns()

CSV_COLUMNS = [
    "iso_timestamp", "unix_time", "channel",
    "fwhm_seconds", "fwhm_source", "fwhm_x_seconds", "fwhm_y_seconds",
    "xy_ratio", "mean_fwhm_seconds", "fwhm_spread",
    "fit_r_squared", "signal_to_noise",
    "n_peaks", "n_resolved",
    "smooth_window", "envelope_samples",
    "xincr", "points", "transfer_seconds",
    "scope_pwidth_seconds",
    "bpm_calibration", "mm_per_second",
    "fwhm_mm", "fwhm_x_mm", "fwhm_y_mm",
    *LEVEL_COLUMNS,
    "tail_ratio_x", "tail_ratio_y",
    "peak_details_json",
]


class ProfileLogger:
    """One session's CSV + JSON sidecar + waveform dump for scope profiles.

    Parameters
    ----------
    output_dir : override for testing (default: ~/Desktop/RBL_log/data/scope/).
    """

    def __init__(self, *, output_dir: Path = None):
        self._run_id    = _new_run_id()
        self._output_dir = Path(output_dir) if output_dir else _output_dir()
        self._output_dir.mkdir(parents=True, exist_ok=True)

        self._csv_path  = self._output_dir / f"{self._run_id}.csv"
        self._wave_path = self._output_dir / f"{self._run_id}.waveforms.csv"
        self._meta_path = self._output_dir / f"{self._run_id}.json"

        self._metadata: dict = {
            "run_id":              self._run_id,
            "start_timestamp_iso": _now_iso(),
        }

        self._file   = open(self._csv_path, "w", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._file, fieldnames=CSV_COLUMNS)
        self._writer.writeheader()
        self._file.flush()

        self._wave_file   = open(self._wave_path, "w", newline="", encoding="utf-8")
        self._wave_writer = csv.writer(self._wave_file)
        self._wave_count  = 0

        self._row_count = 0
        self._closed    = False

    def write_state(self, state) -> None:
        """Append one ScopeState as a stats row + waveform columns."""
        if self._closed:
            raise RuntimeError(
                f"write_state after close() on {self._csv_path}"
            )

        iso = _now_iso()

        peak_details = []
        for p in state.peaks:
            peak_details.append({
                "axis":         p.get("axis", ""),
                "fwhm_seconds": p.get("fwhm_seconds"),
                "fwhm_mm":      p.get("fwhm_mm"),
                # The whole ladder, including WHERE each number came from -
                # a measured width and a fit-derived stand-in are different
                # kinds of claim, and a log that flattened them together
                # could not be re-read honestly a month later.
                "levels":       {lbl: {"seconds": rec.get("seconds"),
                                       "mm":      rec.get("mm"),
                                       "source":  rec.get("source"),
                                       "fit_seconds": rec.get("fit_seconds")}
                                 for lbl, rec in (p.get("levels") or {}).items()},
                "tail_ratio":   p.get("tail_ratio"),
                "resolved":     p.get("resolved"),
                "note":         p.get("note", ""),
            })

        row = {
            "iso_timestamp":       iso,
            "unix_time":           f"{state.timestamp:.3f}",
            "channel":             state.channel,
            "fwhm_seconds":        _safe(state.fwhm_seconds),
            "fwhm_source":         state.fwhm_source,
            "fwhm_x_seconds":      _safe(state.fwhm_x_seconds),
            "fwhm_y_seconds":      _safe(state.fwhm_y_seconds),
            "xy_ratio":            _safe(state.xy_ratio),
            "mean_fwhm_seconds":   _safe(state.mean_fwhm_seconds),
            "fwhm_spread":         _safe(state.fwhm_spread),
            "fit_r_squared":       _safe(state.fit_r_squared),
            "signal_to_noise":     _safe(state.signal_to_noise),
            "n_peaks":             state.n_peaks,
            "n_resolved":          state.n_resolved,
            "smooth_window":       state.smooth_window,
            "envelope_samples":    state.envelope_samples,
            "xincr":               _safe(state.xincr),
            "points":              state.points,
            "transfer_seconds":    _safe(state.transfer_seconds),
            "scope_pwidth_seconds": _safe(state.scope_pwidth_seconds),
            "bpm_calibration":     getattr(state, "calibration_name", ""),
            "mm_per_second":       _safe(getattr(state, "mm_per_second",
                                                 math.nan)),
            "fwhm_mm":             _safe(getattr(state, "fwhm_mm", math.nan)),
            "fwhm_x_mm":           _safe(getattr(state, "fwhm_x_mm", math.nan)),
            "fwhm_y_mm":           _safe(getattr(state, "fwhm_y_mm", math.nan)),
            "tail_ratio_x":        _safe(getattr(state, "tail_ratio_x",
                                                 math.nan)),
            "tail_ratio_y":        _safe(getattr(state, "tail_ratio_y",
                                                 math.nan)),
            "peak_details_json":   json.dumps(peak_details, default=str),
        }

        # Width-ladder columns. Only MEASURED widths are written: a
        # fit-derived stand-in lives in peak_details_json where its source
        # travels with it, and a flat column that silently mixed the two
        # would be unanalysable later.
        by_axis = {p.get("axis"): p for p in state.peaks}
        for level_col in LEVEL_COLUMNS:
            row[level_col] = ""
        for axis, peak in by_axis.items():
            for lbl, rec in (peak.get("levels") or {}).items():
                if lbl == "FWHM" or not rec.get("resolved"):
                    continue
                stem = f"{_slug(lbl)}_{str(axis).lower()}"
                if f"{stem}_seconds" in row:
                    row[f"{stem}_seconds"] = _safe(rec.get("seconds"))
                    row[f"{stem}_mm"]      = _safe(rec.get("mm"))

        self._writer.writerow(row)
        self._file.flush()
        self._row_count += 1

        # Write the waveform: first row is a header label, then voltage values.
        # corrected_downsampled is the analysed trace; volts_downsampled is raw.
        waveform = state.corrected_downsampled or state.volts_downsampled
        if waveform:
            label = f"shot_{self._wave_count:04d}_{iso}"
            self._wave_writer.writerow([label] + [f"{v:.6g}" for v in waveform])
            self._wave_file.flush()
            self._wave_count += 1

    def update_metadata(self, **fields):
        self._metadata.update(fields)

    def close(self) -> str:
        """Finalise CSVs and write the JSON sidecar. Idempotent."""
        if self._closed:
            return str(self._csv_path)
        self._metadata["end_timestamp_iso"] = _now_iso()
        self._metadata["total_shots"]       = self._row_count
        self._metadata["waveforms_saved"]   = self._wave_count

        for f in (self._file, self._wave_file):
            try:
                f.close()
            except Exception:
                log.exception("profile_logger: failed closing %s", f.name)
        self._closed = True

        try:
            with open(self._meta_path, "w", encoding="utf-8") as f:
                json.dump(self._metadata, f, indent=2, default=str)
        except Exception:
            log.exception("profile_logger: failed writing sidecar %s",
                          self._meta_path)
        return str(self._csv_path)

    @property
    def csv_path(self) -> str:
        return str(self._csv_path)
