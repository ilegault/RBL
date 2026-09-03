"""
dynamic_adjustment_history.py
Persistent, append-only log of Dynamic Adjustment trials
(rbl/services/dynamic_adjustment.py) — Section 8.5 point 1: "store raw edge
samples, not just derived metrics," so a campaign never has to be re-run
just because the operator changes their mind about which metric matters.

Same shape and reasoning as trip_history.py/conditioning_history.py: one
JSON line per trial, appended as it completes, never rewritten.
"""
import json
from pathlib import Path

from rbl.config.paths import DYNAMIC_ADJUSTMENT_HISTORY_PATH


def append_trial(record: dict, path: Path = None) -> None:
    """Append one completed trial's record. Never raises."""
    path = path or DYNAMIC_ADJUSTMENT_HISTORY_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception:
        pass


def load_trials(path: Path = None) -> list:
    """Every trial ever logged, oldest first. Returns [] on any failure."""
    path = path or DYNAMIC_ADJUSTMENT_HISTORY_PATH
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


def trials_for(amp_label: str, path: Path = None) -> list:
    """This channel's trials only, oldest first."""
    return [t for t in load_trials(path) if t.get("amp_label") == amp_label]


def winner_for(amp_label: str, path: Path = None):
    """The trial with the lowest (best) figure_of_merit for this channel, or
    None if no trial has a usable score. Section 8.3: "declare a winner by
    figure of merit" — this is that declaration, made from whatever has been
    persisted so far rather than only the trials of one live session."""
    candidates = [t for t in trials_for(amp_label, path)
                  if t.get("figure_of_merit") == t.get("figure_of_merit")]  # not NaN
    if not candidates:
        return None
    return min(candidates, key=lambda t: t["figure_of_merit"])


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "dynamic_adjustment_history.jsonl"

        assert load_trials(p) == []
        assert winner_for("X+", path=p) is None
        print("[OK] missing file reads back empty, no winner")

        append_trial({"amp_label": "X+", "pot_position": "1 o'clock", "figure_of_merit": 5.0}, path=p)
        append_trial({"amp_label": "X+", "pot_position": "2 o'clock", "figure_of_merit": 2.0}, path=p)
        append_trial({"amp_label": "X+", "pot_position": "3 o'clock", "figure_of_merit": 8.0}, path=p)
        append_trial({"amp_label": "Y-", "pot_position": "1 o'clock", "figure_of_merit": 1.0}, path=p)

        x_trials = trials_for("X+", path=p)
        assert len(x_trials) == 3
        winner = winner_for("X+", path=p)
        assert winner["pot_position"] == "2 o'clock"
        print(f"[OK] winner_for picks the lowest figure_of_merit: {winner['pot_position']}")

        assert len(trials_for("Y-", path=p)) == 1
        print("[OK] trials_for filters by channel correctly")

    print("\n[OK] dynamic_adjustment_history self-test passed")
