"""
edge_return_writer.py
CSV for one edge-return test (homing repeatability) on one axis.

WHY THIS EXISTS
---------------
The test's console summary is the quick answer; the CSV is the record the
operator keeps when deciding whether a slit is losing steps or its home switch
is merely jittery.  It holds every reading, with the delta from that speed's
first reading, so the three pass speeds can be compared afterwards.  The file
is written once, at the end of the run, and is flushed and closed before the
path is returned, so a caller can open it immediately.  The directory is read
from rbl.config.paths at call time so tests can redirect it.
"""
import csv
from datetime import datetime
from pathlib import Path

from rbl.config import hardware_config as SC
from rbl.config import paths
from rbl.hardware.edge_return import EdgeReturnReading

CSV_COLUMNS = [
    "axis",
    "slit",
    "pass_speed_cps",
    "cycle",
    "stop_counts",
    "stop_delta_from_first_counts",
    "edge_counts",
    "edge_delta_from_first_counts",
]


def write_edge_return_csv(
    axis_letter: str,
    runs: list[tuple[int, list[EdgeReturnReading]]],
    directory: Path | None = None,
    now: datetime | None = None,
) -> Path:
    """Write one row per reading; `runs` is [(pass_speed_cps, [EdgeReturnReading, ...]), ...]."""
    directory = Path(paths.HOMING_DIR if directory is None else directory)
    now = now or datetime.now()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"edge_return_{axis_letter}_{now:%Y%m%d_%H%M%S}.csv"
    slit = SC.AXIS_NAMES[axis_letter]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(CSV_COLUMNS)
        for speed, readings in runs:
            if not readings:
                continue
            first = readings[0]
            for i, reading in enumerate(readings):
                w.writerow([
                    axis_letter,
                    slit,
                    speed,
                    i + 1,
                    reading.stop_counts,
                    reading.stop_counts - first.stop_counts,
                    reading.edge_counts,
                    reading.edge_counts - first.edge_counts,
                ])
    return path
