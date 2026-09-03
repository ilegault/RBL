"""
trip_history.py
Persistent, append-only log of confirmed regulation faults
(rbl/hardware/regulation.py), across sessions.

WHY THIS EXISTS
---------------
Section 7.4 of docs/AMP_ENVELOPE_AND_HV_SAFETY_PLAN.md calls this the
highest-value artefact the regulation detector produces: over months it
becomes an empirical map of where the real envelope is, built from actual
failures rather than predictions. That only works if every confirmed fault
lands here, unconditionally, regardless of what else is happening in the app.

WHY A FLAT JSONL FILE
----------------------
One record per line, appended as it happens, never rewritten: a fault mid-
write must not corrupt every fault recorded before it (the way rewriting a
whole JSON array would risk), and a new field added to future records needs
no migration — a reader simply ignores keys it doesn't recognise in old ones.
"""
import json
from pathlib import Path

from rbl.config.paths import TRIP_HISTORY_PATH


def append_trip(record: dict, path: Path = None) -> None:
    """Append one fault record as a JSON line.

    Never raises: a failure to log a fault must not prevent the rest of the
    fault response (stopping the channel, telling the operator) from
    completing, the same "logging can't be allowed to block safety" reasoning
    behind `AmpDrive.zero_and_off_all()`'s per-channel try/except.
    """
    path = path or TRIP_HISTORY_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception:
        pass


def load_trip_history(path: Path = None) -> list:
    """Every record ever logged, oldest first.

    Returns [] on any failure (missing file, corrupt line) rather than
    raising — a bad or missing history file must never prevent the app from
    starting. A single corrupt line is skipped, not fatal to the rest.
    """
    path = path or TRIP_HISTORY_PATH
    records = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        pass
    return records


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "sub" / "trip_history.jsonl"

        assert load_trip_history(p) == []
        print("[OK] missing file reads back as empty, not an error")

        append_trip({"label": "X+", "state": "amp_off"}, path=p)
        append_trip({"label": "Y-", "state": "current_limited"}, path=p)
        records = load_trip_history(p)
        assert len(records) == 2
        assert records[0]["label"] == "X+"
        assert records[1]["state"] == "current_limited"
        print("[OK] append_trip/load_trip_history round-trip, order preserved")

        with open(p, "a", encoding="utf-8") as f:
            f.write("not json\n")
        records2 = load_trip_history(p)
        assert len(records2) == 2
        print("[OK] a corrupt line is skipped, not fatal to the rest")

    print("\n[OK] trip_history self-test passed")
