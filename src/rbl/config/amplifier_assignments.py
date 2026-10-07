"""
amplifier_assignments.py
Append-only history of which amplifier drove which plate position, and of
hardware changes that may have altered the load.

WHY THIS EXISTS
---------------
The four EEL5000 amplifiers can be moved between plate positions (the operator
plans to swap X and Y), but every load measurement is a property of the
AMPLIFIER that produced it, not of the position it happened to be plugged into.
Keyed by position, "X+ measured 1612 pF" silently starts meaning a different
unit after a swap, and the question "does a bad channel follow the amplifier or
stay with the plate?" cannot be answered. So each record ties plate positions
to amplifier serial numbers, with a date, and the history module answers "who
was at X+ on that day?".

Hardware changes (a new cable, a cleaned feedthrough) are a separate record
kind: they carry no mapping, only a date and a note, and mark every older
measurement as possibly no longer describing the load.

WHY APPEND-ONLY JSONL
---------------------
Same reasoning as trip_history.py: one record per line, never rewritten, so a
crash mid-write cannot damage earlier records and a record added in a later
version needs no migration. Records are never edited or deleted - a wrong entry
is corrected by recording the right one afterwards.

TIME
----
Nothing here reads a clock. Every function that needs a time takes it as an
argument, so ages and "in force at" questions are tested by passing datetimes.
Naive datetimes are taken as local time so naive and aware values can be
compared.

THE PATH
--------
Read through `rbl.config.paths` AT CALL TIME (`paths.AMPLIFIER_ASSIGNMENTS_STORE`),
never copied into a constant at import, so the autouse fixture in
tests/conftest.py that redirects it to a temp file also covers this module.

Unlike trip_history, a failed write RAISES: a swap that was not recorded is a
wrong answer to every later question, and the operator must be told.
"""
import json
import logging
from datetime import datetime

from rbl.config import paths
from rbl.config.hardware_config import AMP_LABELS

log = logging.getLogger(__name__)

KIND_INITIAL = "initial"
KIND_SWAP = "swap"
KIND_HARDWARE_CHANGE = "hardware_change"
_ASSIGNMENT_KINDS = (KIND_INITIAL, KIND_SWAP)


def _comparable(when: datetime) -> datetime:
    """An aware datetime for ordering (naive values are local time)."""
    return when if when.tzinfo is not None else when.astimezone()


def _append(record: dict) -> None:
    path = paths.AMPLIFIER_ASSIGNMENTS_STORE
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def record_assignment(mapping: dict, when: datetime, kind: str,
                      note: str = "") -> None:
    """Append an `initial` or `swap` record naming all four plate positions."""
    if kind not in _ASSIGNMENT_KINDS:
        raise ValueError(
            f"kind must be one of {_ASSIGNMENT_KINDS}, got {kind!r}")
    if set(mapping) != set(AMP_LABELS):
        raise ValueError(
            f"mapping must name exactly the plate positions {AMP_LABELS}; "
            f"got {sorted(mapping)}")
    for pos, serial in mapping.items():
        if not isinstance(serial, str) or not serial.strip():
            raise ValueError(f"plate position {pos} needs an amplifier serial")
    _append({
        "kind": kind,
        "when": when.isoformat(),
        "mapping": {pos: mapping[pos].strip() for pos in AMP_LABELS},
        "note": note,
    })


def record_hardware_change(when: datetime, note: str) -> None:
    """Append a `hardware_change` record (a date and a note, no mapping)."""
    _append({
        "kind": KIND_HARDWARE_CHANGE,
        "when": when.isoformat(),
        "note": note,
    })


def history() -> list:
    """Every record ever written, in file order.

    A missing file is an empty history. A corrupt line is skipped with a
    warning, never raised: a damaged line must not hide the rest of the history
    or stop the application starting.
    """
    path = paths.AMPLIFIER_ASSIGNMENTS_STORE
    records = []
    try:
        with open(path, encoding="utf-8") as f:
            for n, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    datetime.fromisoformat(rec["when"])
                    records.append(rec)
                except Exception:
                    log.warning("Skipping corrupt line %d in %s", n, path)
    except FileNotFoundError:
        pass
    return records


def assignment_at(when: datetime) -> dict | None:
    """The plate-position -> serial mapping in force at `when`.

    That is the latest `initial`/`swap` record dated at or before `when`; of
    two records with the same date, the one written later wins. None before the
    first record.
    """
    cutoff = _comparable(when)
    best = None
    best_key = None
    for i, rec in enumerate(history()):
        if rec.get("kind") not in _ASSIGNMENT_KINDS:
            continue
        t = _comparable(datetime.fromisoformat(rec["when"]))
        if t > cutoff:
            continue
        key = (t, i)
        if best_key is None or key > best_key:
            best, best_key = rec, key
    return dict(best["mapping"]) if best is not None else None


def current_assignment(now: datetime) -> dict | None:
    """The assignment in force at `now`."""
    return assignment_at(now)


def latest_hardware_change(now: datetime) -> datetime | None:
    """Date of the newest hardware change dated at or before `now`, or None."""
    cutoff = _comparable(now)
    best = None
    for rec in history():
        if rec.get("kind") != KIND_HARDWARE_CHANGE:
            continue
        t = datetime.fromisoformat(rec["when"])
        if _comparable(t) > cutoff:
            continue
        if best is None or _comparable(t) > _comparable(best):
            best = t
    return best
