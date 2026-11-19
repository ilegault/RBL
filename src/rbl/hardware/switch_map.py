"""
switch_map.py
Pure analysis and formatting for the slit switch-map measurement.

WHY THIS EXISTS
---------------
On the Right Beamline slits, the home switch and reverse limit sit inside a
packaged optical switch box and trip at nearly the same point, with hysteresis
between trip and release.  The switch map steps slowly in through both switches
and back out at rest; this module turns those recorded rows into trip/release
positions, the gap between them, and operator-readable summary lines.

SCOPE
-----
Pure functions on plain dataclasses.  No Galil, no Qt, no file I/O.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class SwitchMapRow:
    step: int
    direction: str  # "in" or "out"
    position_counts: int
    home: bool
    reverse: bool
    forward: bool


@dataclass(frozen=True)
class SwitchEdges:
    home_trip: int | None
    reverse_trip: int | None
    home_release: int | None
    reverse_release: int | None


def find_switch_edges(rows: list[SwitchMapRow]) -> SwitchEdges:
    """Find the first inward trip and first outward release for home and reverse."""
    home_trip: int | None = None
    reverse_trip: int | None = None
    home_release: int | None = None
    reverse_release: int | None = None

    prev_home = False
    prev_reverse = False

    for row in rows:
        if row.direction == "in":
            if home_trip is None and row.home and not prev_home:
                home_trip = row.position_counts
            if reverse_trip is None and row.reverse and not prev_reverse:
                reverse_trip = row.position_counts
        elif row.direction == "out":
            if home_release is None and not row.home and prev_home:
                home_release = row.position_counts
            if reverse_release is None and not row.reverse and prev_reverse:
                reverse_release = row.position_counts
        prev_home = row.home
        prev_reverse = row.reverse

    return SwitchEdges(
        home_trip=home_trip,
        reverse_trip=reverse_trip,
        home_release=home_release,
        reverse_release=reverse_release,
    )


def switch_change_line(
    axis: str,
    direction: str,
    switch_name: str,
    tripped: bool,
    position: int,
) -> str:
    """Format one switch transition line for the Command Console."""
    action = "trips" if tripped else "releases"
    return f"{axis}: {direction} — {switch_name} {action} at {position} cts"


def _fmt_pos(pos: int | None) -> str:
    return "never" if pos is None else str(pos)


def switch_map_summary(axis: str, edges: SwitchEdges, inward_stop: str) -> str:
    """Format the end-of-run switch-map summary line."""
    h = _fmt_pos(edges.home_trip)
    r = _fmt_pos(edges.reverse_trip)
    if edges.home_trip is not None and edges.reverse_trip is not None:
        gap = str(edges.reverse_trip - edges.home_trip)
    else:
        gap = "—"
    hr = _fmt_pos(edges.home_release)
    rr = _fmt_pos(edges.reverse_release)
    return (
        f"{axis}: in — home trips at {h}, reverse trips at {r} (gap {gap}); "
        f"out — home releases at {hr}, reverse releases at {rr}; "
        f"inward stop: {inward_stop}"
    )
