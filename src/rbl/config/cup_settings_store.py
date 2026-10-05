"""
cup_settings_store.py
Operator-editable acquisition settings for the Faraday cup, persisted across sessions.

WHY THIS EXISTS
---------------
On the control PC the application is a PyInstaller build, so constants in cup_config.py
cannot be changed without a code edit and a full rebuild. If the beam is weaker than
the arm threshold at the bench, an arm threshold that cannot be changed silently
produces zero acquisition runs, and nothing downstream can recover the lost data.
Similarly, the autorange settle window and sampling cycle schedule need to be
configurable at the bench without modifying the source tree.

This module defines the canonical CupSettings structure and its persistence path.
Defaults come exclusively from rbl.config.cup_config; no default numbers are duplicated here.

WHY SEPARATE FROM cup_config.py
--------------------------------
cup_config.py holds numbers only: the physical and hardware baseline defaults.
This store manages the runtime and persisted configuration that an operator may
alter between or during runs. Keeping the settings shape and its defaults here
ensures a clean boundary between immutable baseline constants and operator-managed state.
"""
from dataclasses import dataclass

from rbl.config.cup_config import (
    CUP_ARM_DEBOUNCE_S,
    CUP_ARM_THRESHOLD_A,
    CUP_CYCLE_DWELL_S,
    CUP_CYCLE_PERIOD_S,
    CUP_RELEASE_INTERVAL_S,
    CUP_RELEASE_THRESHOLD_A,
    CUP_SETTLE_WINDOW_S,
)
from rbl.config.paths import CUP_SETTINGS_STORE

STORE_PATH = CUP_SETTINGS_STORE


@dataclass(frozen=True)
class CupSettings:
    """Frozen acquisition settings for Faraday cup monitoring and sampling cycle."""

    arm_threshold_a: float
    release_threshold_a: float
    settle_window_s: float
    cycle_period_s: float
    cycle_dwell_s: float
    arm_debounce_s: float
    release_interval_s: float

    @classmethod
    def defaults(cls) -> "CupSettings":
        """Return a CupSettings instance populated with baseline defaults from cup_config."""
        return cls(
            arm_threshold_a=CUP_ARM_THRESHOLD_A,
            release_threshold_a=CUP_RELEASE_THRESHOLD_A,
            settle_window_s=CUP_SETTLE_WINDOW_S,
            cycle_period_s=CUP_CYCLE_PERIOD_S,
            cycle_dwell_s=CUP_CYCLE_DWELL_S,
            arm_debounce_s=CUP_ARM_DEBOUNCE_S,
            release_interval_s=CUP_RELEASE_INTERVAL_S,
        )
