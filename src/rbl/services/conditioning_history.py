"""
conditioning_history.py
Persistent, append-only log of completed HV conditioning sessions
(rbl/services/hv_conditioner.py) — Section 6.2 point 6: "persist curves so
successive conditioning sessions can be compared; improvement across
sessions is the signal that conditioning is working."

Same shape and reasoning as rbl/services/trip_history.py: one JSON line per
session, appended as it completes, never rewritten.
"""
import json
from pathlib import Path

from rbl.config.paths import CONDITIONING_HISTORY_PATH


def append_session(record: dict, path: Path = None) -> None:
    """Append one completed session's record. Never raises."""
    path = path or CONDITIONING_HISTORY_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception:
        pass


def load_sessions(path: Path = None) -> list:
    """Every session ever logged, oldest first. Returns [] on any failure."""
    path = path or CONDITIONING_HISTORY_PATH
    records = []
    try:
        with open(path) as f:
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


def sessions_for(amp_label: str, path: Path = None) -> list:
    """This channel's sessions only, oldest first — the sequence a reviewer
    compares to see whether conditioning is actually improving over time."""
    return [s for s in load_sessions(path) if s.get("amp_label") == amp_label]


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "conditioning_history.jsonl"

        assert load_sessions(p) == []
        print("[OK] missing file reads back empty")

        append_session({"amp_label": "X+", "achieved_kv": 2.0, "target_kv": 5.0}, path=p)
        append_session({"amp_label": "X+", "achieved_kv": 3.5, "target_kv": 5.0}, path=p)
        append_session({"amp_label": "Y-", "achieved_kv": 1.0, "target_kv": 5.0}, path=p)

        all_sessions = load_sessions(p)
        assert len(all_sessions) == 3
        x_sessions = sessions_for("X+", path=p)
        assert len(x_sessions) == 2
        assert x_sessions[1]["achieved_kv"] == 3.5
        print("[OK] append_session/load_sessions/sessions_for round-trip, "
              f"X+ improved {x_sessions[0]['achieved_kv']} -> {x_sessions[1]['achieved_kv']} kV")

    print("\n[OK] conditioning_history self-test passed")
