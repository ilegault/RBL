"""
test_dose_model.py
Unit tests for the Faraday cup dose chain, fluence, dpa, and DoseAccumulator.

Covers:
  * Pure functions in rbl/hardware/dose_model.py: no PySide6 import, no clock calls.
  * Settle window sample exclusion: samples during autoranging are excluded from
    the mean, and the excluded count is reported rather than silently dropped.
  * Hand-worked arithmetic: charge, fluence, dpa, and patch area.
  * Zero-current and boundary guards: producing 0.0 without NaNs or ZeroDivisionError.
  * Full worked chain: exact matching of the spec's hand-worked reference numbers
    (I=1.0e-9 A, dt=300.0 s -> Q=3.0e-7 C; patch 5.0x10.0 mm -> A=0.5 cm²;
     q=3, e=1.602176634e-19 -> phi=1.248302e12; k=1.0e-15 -> dpa=1.248302e-3).
  * Pure DoseAccumulator across multiple insertions and intervals.
  * Docstrings state the zero-order hold approximation and unbounded error.
"""
from __future__ import annotations

import ast
import math

import rbl.hardware.dose_model as dm
from rbl.config.cup_config import CUP_SETTLE_WINDOW_S, ELEMENTARY_CHARGE_C
from rbl.hardware.dose_model import (
    DoseAccumulator,
    InsertionCurrentStats,
    compute_charge,
    compute_dpa,
    compute_fluence,
    compute_insertion_current,
    patch_area_cm2,
)


def test_no_pyside6_in_hardware_layer() -> None:
    """Verify rbl/hardware/dose_model.py does not import PySide6 or Qt."""
    with open(dm.__file__, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=dm.__file__)

    imported_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    for mod in imported_modules:
        assert not mod.startswith("PySide6"), f"dose_model imports PySide6: {mod}"
        assert not mod.startswith("rbl.gui"), f"dose_model imports rbl.gui: {mod}"
        assert not mod.startswith("rbl.services"), f"dose_model imports rbl.services: {mod}"
        assert not mod.startswith("rbl.state"), f"dose_model imports rbl.state: {mod}"


def test_patch_area_cm2() -> None:
    """Test patch area calculation in cm² from mm inputs."""
    # 5.0 mm x 10.0 mm -> 50 mm² = 0.5 cm²
    assert math.isclose(patch_area_cm2(5.0, 10.0), 0.5, rel_tol=1e-9)
    # 10.0 mm x 10.0 mm -> 100 mm² = 1.0 cm²
    assert math.isclose(patch_area_cm2(10.0, 10.0), 1.0, rel_tol=1e-9)
    # Zero or negative inputs produce 0.0
    assert patch_area_cm2(0.0, 10.0) == 0.0
    assert patch_area_cm2(10.0, -5.0) == 0.0


def test_settle_window_exclusion_and_stats() -> None:
    """Assert samples inside the settle window are excluded and excluded_count is reported."""
    # Settle window = 1.0 s, start_t = 100.0
    # Samples at t = 100.0, 100.4, 100.8 are inside the 1.0 s window (excluded)
    # Samples at t = 101.0, 101.5, 102.0 are post-settle (included)
    samples = [
        (100.0, 50.0e-9),   # inside (settle)
        (100.4, 40.0e-9),   # inside (settle)
        (100.8, 30.0e-9),   # inside (settle)
        (101.0, 1.0e-9),    # included
        (101.5, 2.0e-9),    # included
        (102.0, 3.0e-9),    # included
    ]

    stats: InsertionCurrentStats = compute_insertion_current(
        samples, start_t=100.0, settle_window_s=CUP_SETTLE_WINDOW_S
    )

    assert stats.excluded_count == 3
    assert stats.sample_count == 3
    # Mean of (1.0e-9, 2.0e-9, 3.0e-9) is 2.0e-9
    assert math.isclose(stats.mean_a, 2.0e-9, rel_tol=1e-9)
    # Sample stdev with ddof=1: sqrt(((1-2)^2 + (2-2)^2 + (3-2)^2) / 2) = 1.0e-9
    assert math.isclose(stats.std_a, 1.0e-9, rel_tol=1e-9)


def test_settle_window_with_objects() -> None:
    """Verify compute_insertion_current works with objects having t/timestamp and current."""
    class DummySample:
        def __init__(self, t: float, current: float):
            self.t = t
            self.current = current

    samples = [
        DummySample(0.0, 10e-9),   # inside
        DummySample(0.5, 10e-9),   # inside
        DummySample(1.0, 4.0e-9),  # included
        DummySample(2.0, 6.0e-9),  # included
    ]

    stats = compute_insertion_current(samples, start_t=0.0, settle_window_s=1.0)
    assert stats.excluded_count == 2
    assert stats.sample_count == 2
    assert math.isclose(stats.mean_a, 5.0e-9, rel_tol=1e-9)


def test_settle_window_empty_or_all_excluded() -> None:
    """Empty samples or all-excluded returns 0.0 without errors."""
    stats_empty = compute_insertion_current([])
    assert stats_empty.sample_count == 0
    assert stats_empty.excluded_count == 0
    assert stats_empty.mean_a == 0.0
    assert stats_empty.std_a == 0.0

    samples = [(0.1, 1e-9), (0.5, 2e-9)]
    stats_all_ex = compute_insertion_current(samples, start_t=0.0, settle_window_s=1.0)
    assert stats_all_ex.sample_count == 0
    assert stats_all_ex.excluded_count == 2
    assert stats_all_ex.mean_a == 0.0
    assert stats_all_ex.std_a == 0.0


def test_single_sample_stdev_is_zero() -> None:
    """A single post-settle sample has 0.0 standard deviation."""
    samples = [(1.5, 3.5e-9)]
    stats = compute_insertion_current(samples, start_t=0.0, settle_window_s=1.0)
    assert stats.sample_count == 1
    assert stats.excluded_count == 0
    assert stats.mean_a == 3.5e-9
    assert stats.std_a == 0.0


def test_charge_calculation() -> None:
    """Test compute_charge over intervals and currents."""
    # 2 nA for 100 s -> 2e-7 C
    assert math.isclose(compute_charge(2.0e-9, 100.0), 2.0e-7, rel_tol=1e-9)
    # Zero current or zero interval gives 0.0
    assert compute_charge(0.0, 100.0) == 0.0
    assert compute_charge(2.0e-9, 0.0) == 0.0
    assert compute_charge(2.0e-9, -10.0) == 0.0


def test_fluence_calculation() -> None:
    """Test compute_fluence arithmetic and division guards."""
    q = 1
    e = ELEMENTARY_CHARGE_C
    a = 1.0  # 1 cm²
    charge = 1.602176634e-19  # 1 ion
    phi = compute_fluence(charge, q, a, e_charge_c=e)
    assert math.isclose(phi, 1.0, rel_tol=1e-9)

    # Division by zero guards
    assert compute_fluence(0.0, 1, 1.0) == 0.0
    assert compute_fluence(1.0, 0, 1.0) == 0.0
    assert compute_fluence(1.0, 1, 0.0) == 0.0
    assert compute_fluence(1.0, -1, 1.0) == 0.0


def test_dpa_calculation() -> None:
    """Test compute_dpa arithmetic and guards."""
    phi = 1.0e15
    k = 2.0e-15
    assert math.isclose(compute_dpa(phi, k), 2.0, rel_tol=1e-9)
    assert compute_dpa(0.0, k) == 0.0
    assert compute_dpa(phi, 0.0) == 0.0


def test_full_worked_chain() -> None:
    """Assert the full worked reference chain matches ticket 08 acceptance criteria.

    Parameters from ticket:
      I = 1.0e-9 A held for 300.0 s gives Q = 3.0e-7 C
      patch 5.0 mm x 10.0 mm gives A = 0.5 cm²
      q = 3 and e = 1.602176634e-19 C give phi = 1.248302e12 ions/cm²
      k = 1.0e-15 gives dpa = 1.248302e-3
    """
    current_a = 1.0e-9
    interval_s = 300.0
    q = 3
    k = 1.0e-15

    # 1. Charge Q
    q_c = compute_charge(current_a, interval_s)
    assert math.isclose(q_c, 3.0e-7, rel_tol=1e-9)
    assert f"{q_c:.1e}" == "3.0e-07"

    # 2. Patch area A
    area_cm2 = patch_area_cm2(5.0, 10.0)
    assert math.isclose(area_cm2, 0.5, rel_tol=1e-9)

    # 3. Fluence phi
    phi = compute_fluence(q_c, q, area_cm2)
    # Expected: 3.0e-7 / (3 * 1.602176634e-19 * 0.5) = 1.248301824e12
    assert math.isclose(phi, 1.248302e12, rel_tol=1e-5)
    assert f"{phi:.6e}" == "1.248302e+12"

    # 4. dpa
    dpa = compute_dpa(phi, k)
    assert math.isclose(dpa, 1.248302e-3, rel_tol=1e-5)
    assert f"{dpa:.6e}" == "1.248302e-03"


def test_dose_accumulator_single_insertion_held_across_interval() -> None:
    """Test DoseAccumulator with a single interval."""
    acc = DoseAccumulator()
    assert acc.total_charge_c == 0.0
    assert acc.total_beam_on_s == 0.0
    assert acc.insertion_count == 0

    dq = acc.record_interval(1.0e-9, start_t=0.0, end_t=300.0)
    assert math.isclose(dq, 3.0e-7, rel_tol=1e-9)
    assert math.isclose(acc.total_charge_c, 3.0e-7, rel_tol=1e-9)
    assert math.isclose(acc.total_beam_on_s, 300.0, rel_tol=1e-9)

    # Worked chain through accumulator
    phi = acc.fluence(charge_state=3, area_cm2=0.5)
    assert math.isclose(phi, 1.248302e12, rel_tol=1e-5)
    dpa = acc.dpa(charge_state=3, area_cm2=0.5, displacement_coeff=1.0e-15)
    assert math.isclose(dpa, 1.248302e-3, rel_tol=1e-5)


def test_dose_accumulator_several_insertions_at_different_currents() -> None:
    """Test DoseAccumulator with multiple sequential insertions."""
    acc = DoseAccumulator()

    # Insertion 1: in at t=0, out at t=3, measured 1.0 nA
    # First insertion has no prior retract_t, so beam_on = 0
    dq1 = acc.record_insertion(t_in=0.0, t_out=3.0, mean_current_a=1.0e-9)
    assert dq1 == 0.0
    assert acc.insertion_count == 1
    assert acc.last_out_t == 3.0
    assert acc.last_current_a == 1.0e-9

    # Beam is on from t=3 to t=103 (100 s) at 1.0 nA
    # Insertion 2: in at t=103, out at t=106, measured 2.0 nA
    dq2 = acc.record_insertion(t_in=103.0, t_out=106.0, mean_current_a=2.0e-9)
    # Expected dq2: 1.0e-9 * 100 s = 1.0e-7 C
    assert math.isclose(dq2, 1.0e-7, rel_tol=1e-9)
    assert math.isclose(acc.total_charge_c, 1.0e-7, rel_tol=1e-9)
    assert math.isclose(acc.total_beam_on_s, 100.0, rel_tol=1e-9)
    assert acc.insertion_count == 2

    # Beam is on from t=106 to t=306 (200 s) at 2.0 nA
    # Insertion 3: in at t=306, out at t=309, measured 3.0 nA
    dq3 = acc.record_insertion(t_in=306.0, t_out=309.0, mean_current_a=3.0e-9)
    # Expected dq3: 2.0e-9 * 200 s = 4.0e-7 C
    assert math.isclose(dq3, 4.0e-7, rel_tol=1e-9)
    assert math.isclose(acc.total_charge_c, 5.0e-7, rel_tol=1e-9)
    assert math.isclose(acc.total_beam_on_s, 300.0, rel_tol=1e-9)
    assert acc.insertion_count == 3


def test_dose_accumulator_zero_current_insertion_no_nan() -> None:
    """Assert a zero-current insertion contributes zero charge without producing NaN."""
    acc = DoseAccumulator()
    acc.record_insertion(t_in=0.0, t_out=3.0, mean_current_a=0.0)
    dq = acc.record_insertion(t_in=303.0, t_out=306.0, mean_current_a=0.0)

    assert dq == 0.0
    assert acc.total_charge_c == 0.0
    assert acc.total_beam_on_s == 300.0
    assert not math.isnan(acc.total_charge_c)

    phi = acc.fluence(charge_state=3, area_cm2=0.5)
    assert phi == 0.0
    assert not math.isnan(phi)

    dpa = acc.dpa(charge_state=3, area_cm2=0.5, displacement_coeff=1.0e-15)
    assert dpa == 0.0
    assert not math.isnan(dpa)


def test_dose_accumulator_reset() -> None:
    """Test accumulator reset."""
    acc = DoseAccumulator()
    acc.record_interval(1.0e-9, 0.0, 100.0)
    assert acc.total_charge_c > 0.0
    assert acc.total_beam_on_s > 0.0

    acc.reset()
    assert acc.total_charge_c == 0.0
    assert acc.total_beam_on_s == 0.0
    assert acc.insertion_count == 0
    assert acc.last_out_t is None
    assert acc.last_current_a is None


def test_module_and_accumulator_docstrings_state_zero_order_hold_and_unbounded_error() -> None:
    """Assert docstring explicitly states zero-order hold approximation and unbounded error."""
    import rbl.hardware.dose_model as dm

    module_doc = dm.__doc__ or ""
    class_doc = dm.DoseAccumulator.__doc__ or ""

    assert "zero-order hold" in module_doc.lower()
    assert "unbounded" in module_doc.lower()
    assert "zero-order hold" in class_doc.lower()
    assert "unbounded" in class_doc.lower()
