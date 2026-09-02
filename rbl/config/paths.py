"""
paths.py
Canonical output and config paths for the RBL application.

WHY THIS EXISTS
---------------
Nine places in the codebase independently constructed ~/Desktop/RBL_log/... or
~/.config/rbl/... — any one of them silently diverging from the others would
send data to a different directory.  A single definition here prevents that and
provides a clean way to override the root for testing or for moving output off
the Desktop (which the operator will eventually want).

Set RBL_LOG_ROOT in the environment to override the default log root.
Default paths are byte-identical to what the app wrote before this module.
"""
import os
from pathlib import Path

# ── Log-root (overridable via environment) ────────────────────────────────────

def _log_root() -> Path:
    override = os.environ.get("RBL_LOG_ROOT")
    if override:
        return Path(override)
    return Path.home() / "Desktop" / "RBL_log"


LOG_ROOT: Path   = _log_root()
LOGS_DIR: Path   = LOG_ROOT / "logs"
DATA_DIR: Path   = LOG_ROOT / "data"

# Per-subsystem data subdirectories
CALIBRATION_DIR: Path = DATA_DIR / "calibration"
SCOPE_DIR:        Path = DATA_DIR / "scope"
VACUUM_DIR:       Path = DATA_DIR / "vacuum"

# Per-subsystem history files (JSONL, one record per event)
TRIP_HISTORY_PATH:               Path = DATA_DIR / "trip_history.jsonl"
CONDITIONING_HISTORY_PATH:       Path = DATA_DIR / "conditioning_history.jsonl"
DYNAMIC_ADJUSTMENT_HISTORY_PATH: Path = DATA_DIR / "dynamic_adjustment_history.jsonl"

# ── Config root (~/.config/rbl/) ─────────────────────────────────────────────

CONFIG_DIR:       Path = Path.home() / ".config" / "rbl"
FUNCGEN_CONFIG:   Path = CONFIG_DIR / "funcgen.json"
LOAD_CAL_STORE:   Path = CONFIG_DIR / "load_calibration.json"
