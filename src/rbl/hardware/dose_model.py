"""
dose_model.py
Pure physics and arithmetic for the Faraday cup dose chain and displacement damage (dpa).

WHY THIS EXISTS
---------------
ADR 0003 Decision 7: An irradiation on the Right Beam Line runs for eight hours or
more, while the Faraday cup is inserted periodically for only a few seconds at a
time (sampling cycle) to measure transmitted beam current. The application must
accumulate beam charge, convert it to ion fluence across the sample patch, and
compute the accumulated displacement damage (dpa) traceable to an operator-entered
SRIM displacement coefficient.

STRUCTURAL REQUIREMENTS
-----------------------
- Pure functions over plain floats in rbl/hardware/.
- No Qt imports (PySide6 is strictly forbidden in the hardware layer).
- Pure accumulator object taking timestamps as inputs, never calling the system
  clock (time.time, time.monotonic).
- Testable against numbers worked by hand.

THE FOUR STAGES
---------------
1. Insertion current: post-settle mean and standard deviation of samples taken
   after the settle window (CUP_SETTLE_WINDOW_S = 1.0 s). Samples during autoranging
   are recorded but excluded from the mean; the count of excluded samples is
   reported rather than silently dropped.
2. Accumulated charge Q: zero-order hold across beam-on intervals. Each insertion's
   current is held constant across the beam-on interval between insertions:
   Q = I * dt. The beam-on interval runs BETWEEN insertions, excluding time the cup
   intercepts the beam.
3. Fluence Φ: Φ = Q / (q * e * A), where q is ion charge state, e is the elementary
   charge (1.602176634e-19 C), and A is the irradiated area in cm².
4. Displacement damage: dpa = Φ * k, where k is the displacement coefficient
   (dpa / (ions/cm²)).

ZERO-ORDER HOLD APPROXIMATION AND UNBOUNDED ERROR
-------------------------------------------------
The dose calculation uses a zero-order hold: each insertion's measured current
is held constant across the beam-on interval between insertions. About one
percent of an eight-hour irradiation is measured; the remaining ninety-nine
percent is assumed constant between samples.

The error of this approximation is whatever the ion source and accelerator
drift over time. This error is UNBOUNDED by anything the application can
observe. No downstream analysis can recover what was never sampled. Slit
currents cannot fill the gaps because they are a relative centering signal
read through log amplifiers lacking absolute calibration, not an absolute
beam current. The only operational control is the sampling cycle period.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence

from rbl.config.cup_config import CUP_SETTLE_WINDOW_S, ELEMENTARY_CHARGE_C


@dataclass(frozen=True)
class InsertionCurrentStats:
    """Statistics for samples collected during a single Faraday cup insertion.

    WHY THIS EXISTS
    ---------------
    When the Faraday cup enters the beam, the Keithley 6482 picoammeter takes up to
    1.0 s to autorange and settle to the true transmitted current. Samples taken
    during this settle window are recorded in the raw log but MUST be excluded from
    the mean current used for dose accumulation.

    The excluded sample count is explicitly tracked and reported so operators and
    downstream analysis can confirm that the settle window was applied rather than
    silently dropping data.
    """
    mean_a: float
    std_a: float
    sample_count: int
    excluded_count: int


def patch_area_cm2(width_x_mm: float, height_y_mm: float) -> float:
    """Calculate irradiated patch area in cm² from full dimensions in mm.

    Calipers and the Raster Planner use millimeters (width X, height Y);
    ion fluence calculations use cm². 1 cm² = 100 mm².
    """
    if width_x_mm <= 0.0 or height_y_mm <= 0.0:
        return 0.0
    return (width_x_mm * height_y_mm) / 100.0


def compute_insertion_current(
    samples: Sequence[tuple[float, float] | Any],
    start_t: float | None = None,
    settle_window_s: float = CUP_SETTLE_WINDOW_S,
) -> InsertionCurrentStats:
    """Compute post-settle mean current and sample standard deviation for an insertion.

    Samples taken within `settle_window_s` seconds of `start_t` (or the first
    sample timestamp if `start_t` is None) are excluded from the mean and counted
    in `excluded_count`.

    Parameters:
        samples: Sequence of samples. Each sample can be a (timestamp, current_a)
                 tuple or an object with `t`/`timestamp` and `current` attributes.
        start_t: Optional explicit timestamp of cup insertion / run start. If None,
                 the timestamp of the first sample is used.
        settle_window_s: Settle window duration in seconds (default CUP_SETTLE_WINDOW_S).

    Returns:
        InsertionCurrentStats containing mean_a, std_a, sample_count, excluded_count.
    """
    if not samples:
        return InsertionCurrentStats(
            mean_a=0.0,
            std_a=0.0,
            sample_count=0,
            excluded_count=0,
        )

    # Helper to extract (t, current)
    def _extract_sample(s: Any) -> tuple[float, float | None]:
        if isinstance(s, (tuple, list)) and len(s) >= 2:
            return float(s[0]), float(s[1]) if s[1] is not None else None
        t = getattr(s, "t", getattr(s, "timestamp", None))
        val = getattr(s, "current", None)
        t_float = float(t) if t is not None else 0.0
        val_float = float(val) if val is not None else None
        return t_float, val_float

    first_t, _ = _extract_sample(samples[0])
    t0 = start_t if start_t is not None else first_t

    included_values: list[float] = []
    excluded_count = 0

    for sample in samples:
        t, val = _extract_sample(sample)
        if val is None or not math.isfinite(val):
            excluded_count += 1
            continue
        if t < (t0 + settle_window_s):
            # Inside the autorange settle window
            excluded_count += 1
        else:
            # Post-settle sample
            included_values.append(val)

    sample_count = len(included_values)
    if sample_count == 0:
        return InsertionCurrentStats(
            mean_a=0.0,
            std_a=0.0,
            sample_count=0,
            excluded_count=excluded_count,
        )

    mean_a = sum(included_values) / sample_count
    if sample_count > 1:
        # Sample standard deviation (ddof=1)
        variance = sum((x - mean_a) ** 2 for x in included_values) / (sample_count - 1)
        std_a = math.sqrt(variance)
    else:
        std_a = 0.0

    return InsertionCurrentStats(
        mean_a=mean_a,
        std_a=std_a,
        sample_count=sample_count,
        excluded_count=excluded_count,
    )


def compute_charge(current_a: float, interval_s: float) -> float:
    """Compute accumulated charge in Coulombs from constant current across an interval.

    Q = I * dt.
    Interval is clamped to >= 0.0.
    """
    dt = max(0.0, interval_s)
    if dt == 0.0 or current_a == 0.0:
        return 0.0
    return current_a * dt


def compute_fluence(
    charge_c: float,
    charge_state: int,
    area_cm2: float,
    e_charge_c: float = ELEMENTARY_CHARGE_C,
) -> float:
    """Compute ion fluence in ions/cm² from accumulated charge, charge state, and area.

    Φ = Q / (q * e * A)

    Parameters:
        charge_c: Accumulated beam charge in Coulombs.
        charge_state: Ion charge state q (positive integer >= 1).
        area_cm2: Irradiated sample area in cm² (> 0.0).
        e_charge_c: Elementary charge in Coulombs (default ELEMENTARY_CHARGE_C).

    Returns:
        Fluence in ions/cm². Returns 0.0 if charge is 0 or any denominator term is <= 0.
    """
    if charge_c == 0.0 or charge_state <= 0 or area_cm2 <= 0.0 or e_charge_c <= 0.0:
        return 0.0
    denominator = charge_state * e_charge_c * area_cm2
    return charge_c / denominator


def compute_dpa(fluence_ions_cm2: float, displacement_coeff: float) -> float:
    """Compute displacements per atom (dpa) from fluence and displacement coefficient.

    dpa = Φ * k

    Parameters:
        fluence_ions_cm2: Ion fluence in ions/cm².
        displacement_coeff: SRIM displacement coefficient k in dpa / (ions/cm²).

    Returns:
        Total dpa. Returns 0.0 if fluence or coefficient is 0.0.
    """
    if fluence_ions_cm2 == 0.0 or displacement_coeff == 0.0:
        return 0.0
    return fluence_ions_cm2 * displacement_coeff


class DoseAccumulator:
    """Pure accumulator summing charge, fluence, and dpa across sampling insertions.

    WHY THIS EXISTS
    ---------------
    ADR 0003 Decision 7: An irradiation on the Right Beam Line runs for eight
    hours or more, during which the Faraday cup is inserted periodically for
    a few seconds (sampling cycle) to measure beam current.

    ZERO-ORDER HOLD APPROXIMATION
    -----------------------------
    The dose calculation uses a zero-order hold: each insertion's measured
    current is held constant across the beam-on interval between insertions.
    About one percent of an eight-hour irradiation is measured; the remaining
    ninety-nine percent is assumed constant between samples.

    The error of this approximation is whatever the ion source and accelerator
    drift over time. This error is UNBOUNDED by anything the application can
    observe. No downstream analysis can recover what was never sampled. Slit
    currents cannot fill the gaps because they are a relative centering signal
    read through log amplifiers lacking absolute calibration, not an absolute
    beam current. The only operational control is the sampling cycle period.

    PURE OBJECT: NO QT, NO CLOCK
    -----------------------------
    This class is pure Python. It does not import PySide6 / Qt and does not
    call the system clock (time.time, time.monotonic). All timestamps are
    provided as explicit inputs, ensuring deterministic and instantaneous
    testing across simulated multi-hour irradiation runs.
    """

    def __init__(self) -> None:
        self._total_charge_c: float = 0.0
        self._total_beam_on_s: float = 0.0
        self._insertion_count: int = 0
        self._last_out_t: float | None = None
        self._last_current_a: float | None = None
        self._last_beam_on_s: float = 0.0

    @property
    def total_charge_c(self) -> float:
        """Total accumulated charge in Coulombs."""
        return self._total_charge_c

    @property
    def total_beam_on_s(self) -> float:
        """Total accumulated beam-on exposure time in seconds."""
        return self._total_beam_on_s

    @property
    def last_beam_on_s(self) -> float:
        """Beam-on duration in seconds preceding the most recent insertion."""
        return self._last_beam_on_s

    @property
    def insertion_count(self) -> int:
        """Count of insertions processed."""
        return self._insertion_count

    @property
    def last_current_a(self) -> float | None:
        """Most recent insertion's mean current in Amperes, or None."""
        return self._last_current_a

    @property
    def last_out_t(self) -> float | None:
        """Timestamp of the most recent cup retraction, or None."""
        return self._last_out_t

    def record_interval(self, current_a: float, start_t: float, end_t: float) -> float:
        """Add charge across a known beam-on interval [start_t, end_t].

        Parameters:
            current_a: Beam current held across the interval in Amperes.
            start_t: Start timestamp in seconds.
            end_t: End timestamp in seconds.

        Returns:
            Delta charge in Coulombs accumulated by this interval.
        """
        dt = max(0.0, end_t - start_t)
        dq = compute_charge(current_a, dt)
        self._total_charge_c += dq
        self._total_beam_on_s += dt
        return dq

    def record_insertion(self, t_in: float, t_out: float, mean_current_a: float) -> float:
        """Record an insertion run, holding the previous current over the elapsed beam-on interval.

        Between the previous retraction (`last_out_t`) and this insertion's entry (`t_in`),
        the beam was on and the specimen received dose at the previously measured current.
        The cup is IN between `t_in` and `t_out`, during which the specimen is NOT irradiated.

        Parameters:
            t_in: Confirmed insertion timestamp (cup enters beam).
            t_out: Confirmed retraction timestamp (cup leaves beam).
            mean_current_a: Post-settle mean current measured during this insertion.

        Returns:
            Delta charge in Coulombs accumulated for the preceding beam-on interval.
        """
        dq = 0.0
        dt = 0.0
        if self._last_out_t is not None and self._last_current_a is not None:
            dt = max(0.0, t_in - self._last_out_t)
            dq = compute_charge(self._last_current_a, dt)
            self._total_charge_c += dq
            self._total_beam_on_s += dt

        self._last_beam_on_s = dt
        self._last_out_t = t_out
        self._last_current_a = mean_current_a
        self._insertion_count += 1
        return dq

    def fluence(self, charge_state: int, area_cm2: float) -> float:
        """Compute accumulated ion fluence Φ in ions/cm²."""
        return compute_fluence(self._total_charge_c, charge_state, area_cm2)

    def dpa(self, charge_state: int, area_cm2: float, displacement_coeff: float) -> float:
        """Compute accumulated displacement damage (dpa)."""
        return compute_dpa(self.fluence(charge_state, area_cm2), displacement_coeff)

    def reset(self) -> None:
        """Reset all accumulated totals and state."""
        self._total_charge_c = 0.0
        self._total_beam_on_s = 0.0
        self._insertion_count = 0
        self._last_out_t = None
        self._last_current_a = None
        self._last_beam_on_s = 0.0
