"""
switch_map_writer.py
CSV writer for one slit switch-map measurement on one axis.

WHY THIS EXISTS
---------------
The Command Console shows the switch-map transitions and summary, while the CSV
records every single-count step in both directions so the operator can inspect
the trip/release positions, hysteresis, and any gap between the home switch and
reverse limit.  The directory is read from rbl.config.paths at call time so
tests can redirect it, and the file is flushed and closed before its path is
returned.
"""
import csv
from datetime import datetime
from pathlib import Path

from rbl.config import hardware_config as SC
from rbl.config import paths
from rbl.hardware.switch_map import SwitchMapRow

CSV_COLUMNS = ["step", "direction", "position_counts", "home", "reverse", "forward"]


def write_switch_map_csv(
    axis_letter: str,
    rows: list[SwitchMapRow],
    directory: Path | None = None,
    now: datetime | None = None,
) -> Path:
    """Write one row per step of a switch-map run and return the CSV path."""
    directory = Path(paths.HOMING_DIR if directory is None else directory)
    now = now or datetime.now()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"switch_map_{axis_letter}_{now:%Y%m%d_%H%M%S}.csv"
    slit = SC.AXIS_NAMES[axis_letter]
    comment = (
        f"# axis={axis_letter} slit={slit} "
        f"step_counts={SC.SWITCH_MAP_STEP_COUNTS} "
        f"step_speed_cps={SC.SWITCH_MAP_STEP_SPEED_COUNTS_PER_SEC}"
    )
    with open(path, "w", newline="", encoding="utf-8") as f:
        f.write(comment + "\n")
        w = csv.writer(f)
        w.writerow(CSV_COLUMNS)
        for row in rows:
            w.writerow([
                row.step,
                row.direction,
                row.position_counts,
                int(row.home),
                int(row.reverse),
                int(row.forward),
            ])
    return path
