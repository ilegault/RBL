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
from collections.abc import Mapping
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
FARADAY_CUP_DIR:  Path = DATA_DIR / "faraday_cup"
# Dark Frames taken with the ZWO camera, one file per set.
ZWO_DARKS_DIR:    Path = DATA_DIR / "zwo_darks"

# Per-subsystem history files (JSONL, one record per event)
TRIP_HISTORY_PATH:               Path = DATA_DIR / "trip_history.jsonl"
CONDITIONING_HISTORY_PATH:       Path = DATA_DIR / "conditioning_history.jsonl"
DYNAMIC_ADJUSTMENT_HISTORY_PATH: Path = DATA_DIR / "dynamic_adjustment_history.jsonl"

# ── Config root (~/.config/rbl/) ─────────────────────────────────────────────

CONFIG_DIR:       Path = Path.home() / ".config" / "rbl"
FUNCGEN_CONFIG:   Path = CONFIG_DIR / "funcgen.json"
LOAD_CAL_STORE:   Path = CONFIG_DIR / "load_calibration.json"
CUP_SETTINGS_STORE: Path = CONFIG_DIR / "cup_settings.json"
# Last applied ZWO camera settings and the selected camera kind.
ZWO_SETTINGS_STORE: Path = CONFIG_DIR / "zwo_camera.json"

# Amplifier assignment history (JSONL): which physical amplifier drove which
# plate, and when. Read through this module at call time (paths.X), never
# copied into a module constant, so tests/conftest.py's autouse fixture covers
# every later module that touches it.
AMPLIFIER_ASSIGNMENTS_STORE: Path = CONFIG_DIR / "amplifier_assignments.jsonl"

# One file per load-characterization result, never overwritten.
CHARACTERIZATION_DIR: Path = DATA_DIR / "load_characterization"


def zwo_sdk_dll_path(frozen: bool, bundle_dir: Path | None,
                     repo_root: Path, env: Mapping[str, str]) -> Path:
    """Where ASICamera2.dll is expected. Never checks that the file exists.

    RBL_ZWO_SDK_DLL wins when set and non-empty; else the one-folder build's
    bundle directory; else vendor/zwo in the source tree. Callers pass
    sys.frozen, sys._MEIPASS and os.environ so all three layouts are testable
    on one machine. The DLL is always passed to zwoasi.init by explicit path.
    """
    override = env.get("RBL_ZWO_SDK_DLL")
    if override:
        return Path(override)
    if frozen:
        if bundle_dir is None:
            raise ValueError("bundle_dir is required when frozen")
        return bundle_dir / "ASICamera2.dll"
    return repo_root / "vendor" / "zwo" / "ASICamera2.dll"

