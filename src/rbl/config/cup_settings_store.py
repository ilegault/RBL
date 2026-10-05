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

WHY validate_settings IS THE ONLY PLACE THE RULES LIVE
------------------------------------------------------
The same function judges values read from the settings file and values an operator
commits on the tab, so the two can never disagree about what is allowed. A rejected
value falls back to that field's default and produces one SettingsLoadWarning (ADR 0002
amendment A5): never a silent substitution, never an exception. An absent key is not a
wrong key: it takes its default with no warning.

Fields are judged in a fixed order (arm, release, dwell, period, settle, debounce,
release interval) so a bad dwell and a bad period each report, instead of one rule
masking the other. A cross-field rule compares against the value already accepted
earlier in that order; for a partner not yet judged it uses that partner's raw value if
it is a finite number, else its default; for the dwell, which has two partners, the
operator's pair is tried first and then the defaults, so a bad settle window or period
does not also condemn a good dwell, while two coupled values lowered together (a shorter
dwell with a shorter settle window) are accepted as the pair the operator meant.
The period must hold the dwell plus two confirmed moves (insert and retract), each
allowed CUP_MOVE_CONFIRMATION_TIMEOUT_S.
"""
import math
from dataclasses import dataclass

from rbl.config.cup_config import (
    CUP_ARM_DEBOUNCE_S,
    CUP_ARM_THRESHOLD_A,
    CUP_CYCLE_DWELL_S,
    CUP_CYCLE_PERIOD_S,
    CUP_MOVE_CONFIRMATION_TIMEOUT_S,
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


@dataclass(frozen=True)
class SettingsLoadWarning:
    """One rejected setting: which key, what was found, why, and what replaced it."""

    key: str
    found: str
    reason: str
    fallback: float


_MAX_CYCLE_PERIOD_S = 604800.0  # one week


def _number(value: object) -> float | None:
    """Return value as a finite float, or None for anything else (bool, str, nan...)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    f = float(value)
    return f if math.isfinite(f) else None


def validate_settings(raw: dict) -> tuple[CupSettings, list[SettingsLoadWarning]]:
    """Turn raw values into CupSettings plus one warning per rejected key. Never raises."""
    if not isinstance(raw, dict):
        raw = {}
    defaults = CupSettings.defaults()
    two_moves = 2 * CUP_MOVE_CONFIRMATION_TIMEOUT_S
    accepted: dict[str, float] = {}
    warnings: list[SettingsLoadWarning] = []

    def candidate(key: str) -> float:
        """Raw finite value of a key, else its default (for a partner not yet judged)."""
        n = _number(raw.get(key)) if key in raw else None
        return n if n is not None else getattr(defaults, key)

    def got(key: str) -> float:
        return accepted[key] if key in accepted else candidate(key)

    def judge(key: str, rule) -> None:
        if key not in raw:
            accepted[key] = getattr(defaults, key)
            return
        value = raw[key]
        v = _number(value)
        reason = "not a finite number" if v is None else rule(v)
        if v is not None and reason is None:
            accepted[key] = v
            return
        fallback = getattr(defaults, key)
        accepted[key] = fallback
        try:
            found = repr(value)
        except Exception:  # a hostile __repr__ must not break loading
            found = "<unprintable>"
        warnings.append(SettingsLoadWarning(key, found, reason, fallback))

    judge("arm_threshold_a", lambda v: None if 1e-9 <= v <= 1e-3
          else "must be between 1e-9 A and 1e-3 A")
    judge("release_threshold_a", lambda v: None if 0 < v < accepted["arm_threshold_a"]
          else "must be above 0 and below the arm threshold")
    def dwell_rule(v: float) -> str | None:
        # Judged before its partners, so try the operator's pair first, then the
        # defaults: a bad settle window or period must not also condemn a good dwell.
        for settle, period in (
            (got("settle_window_s"), got("cycle_period_s")),
            (defaults.settle_window_s, defaults.cycle_period_s),
        ):
            if v > settle and v + two_moves < period:
                return None
        return "must exceed the settle window and fit, with two moves, inside the period"

    judge("cycle_dwell_s", dwell_rule)
    judge("cycle_period_s", lambda v: None
          if accepted["cycle_dwell_s"] + two_moves <= v <= _MAX_CYCLE_PERIOD_S
          else "must hold the dwell plus two moves and be at most one week")
    judge("settle_window_s", lambda v: None if 0 <= v < accepted["cycle_dwell_s"]
          else "must be at least 0 and below the dwell")
    judge("arm_debounce_s", lambda v: None if 0 < v <= 60.0
          else "must be above 0 and at most 60 s")
    judge("release_interval_s", lambda v: None if 0 < v <= 60.0
          else "must be above 0 and at most 60 s")

    return CupSettings(**accepted), warnings
