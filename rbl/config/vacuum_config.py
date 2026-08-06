"""
vacuum_config.py
Constants for the two RS-232 vacuum gauge controllers.

No logic lives here — only named values so every tunable number has
exactly one home and a comment explaining the constraint it must satisfy.
"""

# ---- Baud rates -----------------------------------------------------------
# Confirm against the instrument's front-panel setting before first use.
XGS_BAUD_DEFAULT = 9600    # XGS-600 factory default; try 19200 if unresponsive
VGC_BAUD_DEFAULT = 19200   # VGC083 factory default and manual-specified rate

# ---- Polling --------------------------------------------------------------
# Both controllers are polled on the same 1 Hz cycle, interleaved.
# Hard limits from the manuals:
#   XGS-600 : < 10 queries/second (enforced additionally in xgs600_driver.py)
#   VGC083  : max 46 ms repetition rate at 19200 baud
POLL_INTERVAL_S = 1.0

# Minimum gap between successive VGC083 commands (manual: 46 ms at 19200).
# We use 60 ms — the driver already enforces this internally; this constant
# is here for documentation and the worker's inter-channel sleep.
VGC_MIN_GAP_S = 0.060

# Hard ceiling for XGS-600 (manual: < 10 per second).
XGS_MAX_QUERY_HZ = 5.0

# ---- VGC083 channel configuration ----------------------------------------
# Which channels are physically populated. Operator-editable before first run.
# Valid values: "IG" (ion gauge), "CG1" / "CG2" (convection), "AI" (analog).
VGC_ACTIVE_CHANNELS = ["IG", "CG1", "CG2"]

# ---- Display names --------------------------------------------------------
# Human-readable label per gauge, keyed by "<instrument>:<channel-or-label>".
# Filled in by the operator once the physical locations are confirmed.
# Example: {"xgs600:IG1": "Beam line IG", "vgc083:CG1": "Roughing line"}
GAUGE_DISPLAY_NAMES: dict[str, str] = {}

# ---- UI thresholds (cosmetic only) ----------------------------------------
# Pressure below which a reading is highlighted as "good vacuum".
# THIS IS A DISPLAY CONVENTION. It is NOT an interlock and must never gate
# any hardware action.
UI_GOOD_VACUUM_TORR = 1e-6

# ---- Reconnect backoff schedule -------------------------------------------
# Seconds to wait between reconnect attempts after a serial failure.
# The worker advances through this list; the last value repeats indefinitely.
RECONNECT_BACKOFF_S = [1.0, 2.0, 5.0, 10.0, 30.0]

# ---- Stale-data guard (consumed by the vacuum tab) ------------------------
# A snapshot older than this many seconds is shown greyed with its age.
# A frozen number that looks live is the dangerous failure mode.
STALE_THRESHOLD_S = 3.0 * POLL_INTERVAL_S
