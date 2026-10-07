"""
characterization_history.py
Every load characterization result, kept forever, and the queries the planner
and the Load Characterization tab ask of them.

WHY THIS EXISTS
---------------
The old per-channel store (load_calibration_store.py) kept one record per
(plate position, load condition) and overwrote it. That made three questions
unanswerable: how has this load changed over weeks, which physical amplifier a
number belongs to, and how old a number is. This module replaces it with one
JSON file per run, never overwritten, each tagged with the AMPLIFIER (serial),
the plate position, the load condition, the method and the time.

The planner asks for the newest result for the amplifier CURRENTLY assigned to a
plate position, so after an amplifier swap a result measured on the old
amplifier is not silently presented as the new one's - it stays attached to the
amplifier that produced it (`newest_for_amplifier`).

NOTHING IS ASSUMED
------------------
A plate position with no usable result answers None, and callers say "not
measured". There is no fallback capacitance in this module or behind it.

RULES
-----
* A result is never overwritten: a taken name gets `_2`, `_3`... (the same
  convention as `services/log_rollover.unused_path`) and the file is opened
  exclusively. That function is not imported because this module lives in
  `config/`, and imports flow downward only (gui > services > state > hardware >
  config); a config module importing from services would be a new upward import.
* An aborted result stays on disk (an abort is itself a finding) but is never
  returned as "the newest": nothing may plan from a number the run itself
  rejected.
* `newest_on_plates_c_pf` looks at ON_PLATES results that actually carry a
  capacitance, so a newer clamp-test result does not hide it. Cable-only and
  disconnected results are never planning inputs.
* If no amplifier assignment exists at all, results whose serial is
  "unassigned" count as the current amplifier's, so the tool is usable before
  serial numbers are entered. Once any assignment exists they belong to nobody.
* A result dated after `now` is not yet known and is ignored.
* Time is always an argument; nothing here reads a clock.

THE PATH
--------
Read through `rbl.config.paths` AT CALL TIME (`paths.CHARACTERIZATION_DIR`), so
the autouse redirect in tests/conftest.py covers this module.
"""
import json
import logging
from datetime import datetime
from pathlib import Path

from rbl.config import amplifier_assignments, paths
from rbl.config.calibration_config import LoadCondition
from rbl.config.hardware_config import AMP_LABELS

log = logging.getLogger(__name__)

METHODS = ("impedance_sweep", "charge_integral_ladder", "clamp_test")
UNASSIGNED = "unassigned"
_REQUIRED = ("plate_position", "amplifier_serial", "load_condition", "method",
             "values")


def _comparable(when: datetime) -> datetime:
    return when if when.tzinfo is not None else when.astimezone()


def _seconds_between(later: datetime, earlier: datetime) -> float:
    """later - earlier, without a timezone round trip when both are naive."""
    if (later.tzinfo is None) == (earlier.tzinfo is None):
        return (later - earlier).total_seconds()
    return (_comparable(later) - _comparable(earlier)).total_seconds()


def write_result(result: dict, when: datetime) -> Path:
    """Write one result file; return its path. Never overwrites an existing file."""
    for key in _REQUIRED:
        if key not in result:
            raise ValueError(f"result is missing {key!r}")
    if result["plate_position"] not in AMP_LABELS:
        raise ValueError(f"unknown plate position {result['plate_position']!r}")
    if result["load_condition"] not in {c.value for c in LoadCondition}:
        raise ValueError(f"unknown load condition {result['load_condition']!r}")
    if result["method"] not in METHODS:
        raise ValueError(f"unknown method {result['method']!r}; expected one of {METHODS}")
    if not isinstance(result["values"], dict):
        raise ValueError("values must be a dict")
    serial = result["amplifier_serial"]
    if not isinstance(serial, str) or not serial.strip():
        raise ValueError("amplifier_serial must be a non-empty string "
                         f"(use {UNASSIGNED!r} before serials are entered)")

    record = dict(result)
    record["when"] = when.isoformat()
    record["assignment"] = amplifier_assignments.assignment_at(when)

    folder = paths.CHARACTERIZATION_DIR
    folder.mkdir(parents=True, exist_ok=True)
    stem = (f"{record['plate_position']}_{record['method']}_"
            f"{when.strftime('%Y%m%dT%H%M%S')}")
    n = 1
    while True:
        path = folder / (f"{stem}.json" if n == 1 else f"{stem}_{n}.json")
        try:
            with open(path, "x", encoding="utf-8") as f:   # "x": never overwrite
                json.dump(record, f, indent=2, default=str)
            return path
        except FileExistsError:
            n += 1


def _all_results() -> list:
    """Every readable result, oldest first (ties: later file name last)."""
    folder = paths.CHARACTERIZATION_DIR
    out: list[dict] = []
    if not folder.exists():
        return out
    for path in sorted(folder.glob("*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                rec = json.load(f)
            rec["_when"] = datetime.fromisoformat(rec["when"])
            for key in _REQUIRED:
                rec[key]
            out.append(rec)
        except Exception:
            log.warning("Skipping unreadable characterization file %s", path)
    out.sort(key=lambda r: _comparable(r["_when"]))
    return out


def _present(rec: dict, now: datetime) -> dict:
    hw = amplifier_assignments.latest_hardware_change(now)
    shown = {k: v for k, v in rec.items() if k != "_when"}
    shown["age_s"] = _seconds_between(now, rec["_when"])
    shown["predates_hardware_change"] = (
        hw is not None and _comparable(rec["_when"]) < _comparable(hw))
    return shown


def _newest(now, matches, need_value=None):
    best = None
    for rec in _all_results():
        if rec.get("aborted") or _comparable(rec["_when"]) > _comparable(now):
            continue
        if need_value is not None and not isinstance(
                rec["values"].get(need_value), (int, float)):
            continue
        if matches(rec):
            best = rec          # sorted oldest first: the last match is newest
    return None if best is None else _present(best, now)


def _serial_in_force(plate_position: str, now: datetime) -> str:
    assignment = amplifier_assignments.current_assignment(now)
    if assignment is None:
        return UNASSIGNED
    return assignment[plate_position]


def newest(plate_position: str, load_condition: str, now: datetime,
           _need_value: str | None = None) -> dict | None:
    """Newest non-aborted result for the amplifier currently at `plate_position`.

    Adds `age_s` and `predates_hardware_change`. None when there is no usable
    result - the caller says "not measured".
    """
    serial = _serial_in_force(plate_position, now)
    return _newest(
        now,
        lambda r: (r["plate_position"] == plate_position
                   and r["load_condition"] == load_condition
                   and r["amplifier_serial"] == serial),
        need_value=_need_value)


def newest_for_amplifier(serial: str, load_condition: str,
                         now: datetime) -> dict | None:
    """Newest non-aborted result for this amplifier, wherever it was plugged in."""
    return _newest(
        now,
        lambda r: (r["amplifier_serial"] == serial
                   and r["load_condition"] == load_condition))


def newest_with_capacitance(plate_position: str, load_condition: str,
                            now: datetime) -> dict | None:
    """Newest result for this position and condition that carries a capacitance.

    Looks past a newer result that has none (a clamp test), so what is shown is
    the capacitance together with the age of the run that measured it.
    """
    return newest(plate_position, load_condition, now, _need_value="c_pf")


def newest_on_plates(plate_position: str, now: datetime) -> dict | None:
    """`newest_with_capacitance` for ON_PLATES - the load a run actually drives."""
    return newest_with_capacitance(plate_position, LoadCondition.ON_PLATES.value, now)


def newest_on_plates_c_pf(plate_position: str, now: datetime) -> float | None:
    """Capacitance (pF) of the newest ON_PLATES result that carries one, or None."""
    rec = newest_on_plates(plate_position, now)
    return None if rec is None else float(rec["values"]["c_pf"])


_DAY_S = 86400.0
_WEEKS_FROM_DAYS = 60


def age_text(age_s: float) -> str:
    """How old a result is, for display: "today", "1 day ago", "32 days ago",
    and weeks once it is a couple of months old."""
    days = int(max(0.0, age_s) // _DAY_S)
    if days < 1:
        return "today"
    if days == 1:
        return "1 day ago"
    if days < _WEEKS_FROM_DAYS:
        return f"{days} days ago"
    return f"{days // 7} weeks ago"
