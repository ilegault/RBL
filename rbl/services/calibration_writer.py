"""
calibration_writer.py
CSV + JSON metadata sidecar for one calibration run (sweep or drift).

The CSV is written incrementally, flushing after every row: a twelve-hour
drift run that dies at hour eleven must leave eleven hours of usable data on
disk, not an empty file. The JSON sidecar is finalized at close() — most of
its fields (seed, funcgen readback, git hash, end timestamp) are only known
once the run has actually happened, so writing it once at the end (rather
than trying to pre-commit it at open time) is the honest ordering.

The sidecar exists so the CSV is still interpretable a year from now, in
particular the two fields nothing else records: `load_condition` (was the
amplifier driving actual plates or sitting open-circuit?) and each channel's
readback `:OUTPut:LOAD` setting — the driver defaults to INFinity, but the
front panel can override that, and 50 ohm silently halves the delivered
voltage into the EEL5000's high-Z input. If someone changed it by hand, this
field is the only evidence the CSV's numbers should be doubled.
"""
import csv
import json
import logging
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from rbl.config import calibration_config

log = logging.getLogger(__name__)

# Must match the CalibrationRunner row schema exactly, in this order.
CSV_COLUMNS = [
    "run_id", "timestamp_iso", "t_elapsed_s", "pass_index", "pass_type",
    "driven_amp", "commanded_kv", "commanded_gen_v",
    "ain", "amp_label", "kind",
    "mean_v", "std_v", "rms_v", "min_v", "max_v", "abs_p999_v",
    "fund_v", "fund_phase_rad", "crest", "n_cycles",
    "n_samples", "n_windows",
    "converted_value", "converted_unit", "stream_profile",
]
# Two columns added 2026-08; older CSVs simply lack them.
#
#   rms_v      std_v alone is RMS about the MEAN, so it drops the DC term and
#              understates a waveform sitting on an offset. Reconstructible
#              from old files as sqrt(mean_v**2 + std_v**2), since both were
#              computed over the same samples.
#   abs_p999_v 99.9th percentile of |x|. max_v is a single sample out of ~100k
#              and so is pure extreme-value noise; this is the robust peak.
#              NOT reconstructible from old files — it needs the raw samples,
#              which are not retained. Pre-2026-08 runs have max_v only.


def new_run_id() -> str:
    return time.strftime("cal_%Y%m%dT%H%M%S")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def config_snapshot() -> dict:
    """Every public, upper-case constant in calibration_config, as plain data.

    Enums become their .value; Paths become str; nothing else is touched.
    Recorded in every sidecar so a CSV can be re-interpreted correctly even
    after the constants it was produced under have since changed.
    """
    snap = {}
    for name, value in vars(calibration_config).items():
        if not name.isupper():
            continue
        if isinstance(value, Path):
            value = str(value)
        elif hasattr(value, "value") and not isinstance(value, (int, float, str)):
            value = value.value
        snap[name] = value
    return snap


def git_commit_hash() -> str:
    """The checked-out commit hash, or "" if git/the repo isn't available.

    Wrapped so a non-git checkout (or a machine with no git binary) never
    crashes a run over a provenance nicety.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        log.exception("git_commit_hash: git unavailable or not a repo")
    return ""


class CalibrationWriter:
    """Owns one run's CSV + JSON sidecar, both under CAL_OUTPUT_DIR."""

    def __init__(self, run_id: str = None, output_dir=None, metadata: dict = None):
        self.run_id = run_id or new_run_id()
        self.output_dir = Path(output_dir) if output_dir is not None \
            else calibration_config.CAL_OUTPUT_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.csv_path  = self.output_dir / f"{self.run_id}.csv"
        self.meta_path = self.output_dir / f"{self.run_id}.json"

        self.metadata: dict = dict(metadata or {})
        self.metadata.setdefault("run_id", self.run_id)
        self.metadata.setdefault("start_timestamp_iso", now_iso())

        self._file = open(self.csv_path, "w", newline="")
        self._csv  = csv.DictWriter(self._file, fieldnames=CSV_COLUMNS)
        self._csv.writeheader()
        self._file.flush()
        self._closed = False

    def write_row(self, row: dict):
        """Append one row and flush immediately (see module docstring)."""
        if self._closed:
            raise RuntimeError(f"write_row after close() on {self.csv_path}")
        self._csv.writerow({col: row.get(col, "") for col in CSV_COLUMNS})
        self._file.flush()

    def update_metadata(self, **fields):
        """Merge additional fields into the sidecar, before close()."""
        self.metadata.update(fields)

    def close(self) -> str:
        """Finalize the CSV and write the JSON sidecar. Idempotent."""
        if self._closed:
            return str(self.csv_path)
        self.metadata.setdefault("end_timestamp_iso", now_iso())
        try:
            self._file.close()
        except Exception:
            log.exception("close: failed closing %s", self.csv_path)
        self._closed = True

        try:
            with open(self.meta_path, "w") as f:
                json.dump(self.metadata, f, indent=2, default=str)
        except Exception:
            log.exception("close: failed writing sidecar %s", self.meta_path)

        return str(self.csv_path)
