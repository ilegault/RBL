"""
cup_log_totals.py
Pure readers that recover running dose totals from an earlier cup log.

WHY THIS EXISTS
---------------
ADR 0003 amendment C4/C5: an irradiation can span several cup logs (the app was
restarted, the operator opened a new log), and the dose must keep counting rather
than restart at zero. The only record of the running total is the cup log itself,
so these functions read it back.

They live apart from ``cup_log.py`` because that module holds a QObject. Reading a
file is plain Python and tests should be able to prove it stays Qt-free, the way
``test_no_pyside6_in_hardware_layer`` does for the dose model. No clock, no Qt.

WHAT "COUNTED" MEANS
--------------------
Only ``insertion_summary`` rows whose ``counted_in_dose`` is ``true`` carry the
scheduled series' running total (ADR 0003 C4). Manual, forced and uncommanded
insertions are logged but their charge is not in that total, so a log holding only
those has no totals to continue from and reads back as ``None``.

The ``charge`` and ``total_beam_on_s`` columns of a summary row are already running
totals, so the last counted row IS the totals; nothing is summed. The insertion
count is the counted rows in the file plus the count carried over in the
``# continued totals`` header of a log that itself continued an earlier one.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

from rbl.hardware.dose_model import DoseTotals

_CONTINUED_COUNT = re.compile(r"insertion_count=(\d+)")


def _float_or(text: str | None, default: float) -> float:
    try:
        return float(text) if text not in (None, "") else default
    except ValueError:
        return default


def read_dose_totals(csv_path: Path) -> DoseTotals | None:
    """Totals from the last counted summary row of ``csv_path``, else ``None``.

    ``last_out_t`` is that row's confirmed timestamp plus its dwell (the moment the
    cup was withdrawn) and ``last_current_a`` its mean current. An unreadable or
    missing file is ``None``: the caller treats it as "nothing to continue".
    """
    path = Path(csv_path)
    try:
        with open(path, newline="", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return None

    carried = 0
    body: list[str] = []
    for line in lines:
        if line.startswith("#"):
            if line.startswith("# continued totals:"):
                m = _CONTINUED_COUNT.search(line)
                if m:
                    carried = int(m.group(1))
        else:
            body.append(line)

    counted = 0
    last: dict[str, str] | None = None
    for row in csv.DictReader(body):
        if (
            row.get("record_type") == "insertion_summary"
            and (row.get("counted_in_dose") or "").strip().lower() == "true"
        ):
            counted += 1
            last = row
    if last is None:
        return None

    return DoseTotals(
        total_charge_c=_float_or(last.get("charge"), 0.0),
        total_beam_on_s=_float_or(last.get("total_beam_on_s"), 0.0),
        insertion_count=carried + counted,
        last_out_t=_float_or(last.get("confirmed_timestamp"), 0.0)
        + _float_or(last.get("dwell"), 0.0),
        last_current_a=_float_or(last.get("mean_current_a"), 0.0),
        source_path=str(path),
    )


def find_previous_session_cup_log(logs_dir: Path, exclude: Path) -> Path | None:
    """Newest ``session_*/cup.csv`` under ``logs_dir`` that has totals to continue.

    "Newest" is by folder name (session folders are timestamp-named, so name order
    is time order and survives a copied or touched file). ``exclude`` is skipped,
    typically the session being started. Files with no counted row are skipped.
    """
    logs_dir = Path(logs_dir)
    if not logs_dir.is_dir():
        return None
    skip = Path(exclude)
    for folder in sorted(
        (p for p in logs_dir.glob("session_*") if p.is_dir()),
        key=lambda p: p.name,
        reverse=True,
    ):
        candidate = folder / "cup.csv"
        if candidate == skip or not candidate.is_file():
            continue
        if read_dose_totals(candidate) is not None:
            return candidate
    return None
