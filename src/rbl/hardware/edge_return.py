"""
edge_return.py
Analysis of the edge-return test: repeated readings of the commanded position
at the home switch's transition, without redefining zero.

WHY THIS EXISTS
---------------
The operator sees the home switch "somewhere else" when coming back to it and
cannot tell whether that is switch jitter or lost steps.  The two look
different in the data: jitter scatters the readings by a count or two around a
steady value, while a stalled two-phase stepper slips by whole electrical
cycles (four full steps, 8 counts at half-stepping), so lost steps show up as
cycle-to-cycle jumps near a non-zero multiple of 8 counts.  This module turns a
list of edge readings into spread, drift, deviation and a list of such jumps.

SCOPE
-----
Pure math on plain integers.  No Galil, no Qt.  The thresholds live in
rbl/config/hardware_config.py (LOST_STEP_GROUP_COUNTS, LOST_STEP_TOLERANCE_COUNTS).
"""
from dataclasses import dataclass
from math import sqrt

from rbl.config import hardware_config as SC


def is_lost_step_jump(delta_counts: int) -> bool:
    """True when |delta| is within the tolerance of a non-zero multiple of the
    lost-step group size."""
    g = SC.LOST_STEP_GROUP_COUNTS
    tol = SC.LOST_STEP_TOLERANCE_COUNTS
    m = abs(delta_counts)
    nearest = round(m / g) * g
    return nearest >= g and abs(m - nearest) <= tol


@dataclass(frozen=True)
class EdgeReturnReading:
    """One cycle of the edge-return test: where HM stopped and where the switch released."""
    stop_counts: int
    edge_counts: int


@dataclass(frozen=True)
class EdgeReturnSummary:
    n: int
    deltas_counts: tuple[int, ...]
    total_drift_counts: int
    spread_counts: int
    std_counts: float
    lost_step_jumps: tuple[int, ...]


def analyse_edge_returns(edge_counts: list[int]) -> EdgeReturnSummary:
    """Summarise edge readings; needs at least two."""
    n = len(edge_counts)
    if n < 2:
        raise ValueError("edge-return analysis needs at least two readings")
    deltas = tuple(edge_counts[i + 1] - edge_counts[i] for i in range(n - 1))
    mean = sum(edge_counts) / n
    std = sqrt(sum((c - mean) ** 2 for c in edge_counts) / n)
    return EdgeReturnSummary(
        n=n,
        deltas_counts=deltas,
        total_drift_counts=edge_counts[-1] - edge_counts[0],
        spread_counts=max(edge_counts) - min(edge_counts),
        std_counts=std,
        lost_step_jumps=tuple(i for i, d in enumerate(deltas) if is_lost_step_jump(d)),
    )


def summary_line(
    summary: EdgeReturnSummary,
    axis_letter: str,
    pass_speed_cps: int,
    label: str | None = None,
) -> str:
    """One-line result for the Command Console."""
    slit = SC.AXIS_NAMES[axis_letter]
    spread_um = summary.spread_counts / SC.STEPS_PER_MM[axis_letter] * 1000
    tag = f", {label}" if label else ""
    return (
        f"{axis_letter} [{slit}] @ {pass_speed_cps} cps{tag}: n={summary.n}, "
        f"spread {summary.spread_counts} cts ({spread_um:.1f} µm), "
        f"drift {summary.total_drift_counts:+d} cts, σ {summary.std_counts:.2f} cts, "
        f"lost-step jumps: {len(summary.lost_step_jumps)}"
    )
