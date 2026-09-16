"""
test_sampling_cycle.py
Unit tests for the pure SamplingCycleScheduler.

WHY THIS EXISTS
---------------
ADR 0003 Decision 2: the scheduler must be testable without a real clock. Every
timestamp is an explicit argument, so the tests below feed deterministic sequences
and assert on exact insertion counts and times. No test sleeps; the eight-hour
cycle test runs in milliseconds.

Test coverage
-------------
- Construction: disarmed by default, not armed.
- arm / disarm lifecycle.
- stop(): returns CycleRetract when INSERTING, None otherwise; leaves DISARMED.
- Full 8-hour cycle: insertion count == 96, every insertion and retract time exact.
- Manual insert during an armed cycle: manual run opens; next boundary is skipped.
- Scheduled insertion during a manual run: skip recorded; following insertion is not.
- Manual retract closes the manual run; cycle resumes on schedule.
- Manual insert during INSERTING state: dwell abandoned; schedule resets.
- Period change mid-cycle: first boundary unaffected; subsequent boundaries use
  the new period.
- set_dwell: takes effect at next insertion, not mid-dwell.
- time_to_next_insertion: correct in WAITING; None in INSERTING / DISARMED.
- time_to_retract: correct in INSERTING; None in WAITING / DISARMED.
- tick() returns None when disarmed; tick()-tick() handles two events in one call.
- No imports of Qt or time.* in sampling_cycle.py.
"""
import ast
import inspect

import pytest

from rbl.config.cup_config import CUP_CYCLE_DWELL_S, CUP_CYCLE_PERIOD_S
from rbl.services.sampling_cycle import (
    CycleInsert,
    CycleRetract,
    CycleSkipped,
    CycleState,
    SamplingCycleScheduler,
)

# ── Module purity checks ───────────────────────────────────────────────────────


class TestModulePurity:
    """sampling_cycle.py must contain no Qt and no clock calls."""

    def _module_ast(self):
        import rbl.services.sampling_cycle as mod

        return ast.parse(inspect.getsource(mod))

    def test_no_qt_import(self):
        """sampling_cycle.py must contain no Qt import statements."""
        tree = self._module_ast()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("PySide6"), (
                        f"sampling_cycle.py must not import PySide6: {alias.name}"
                    )
                    assert not alias.name.startswith("PyQt"), (
                        f"sampling_cycle.py must not import PyQt: {alias.name}"
                    )
            elif isinstance(node, ast.ImportFrom):
                mod_name = node.module or ""
                assert not mod_name.startswith("PySide6"), (
                    f"sampling_cycle.py must not import from PySide6: {mod_name}"
                )
                assert not mod_name.startswith("PyQt"), (
                    f"sampling_cycle.py must not import from PyQt: {mod_name}"
                )

    def test_no_clock_call(self):
        """sampling_cycle.py must contain no clock calls (time.time, etc.)."""
        tree = self._module_ast()
        forbidden_attrs = {"time", "monotonic", "perf_counter", "now"}
        clock_modules = {"time", "datetime"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                # Catch time.time(), time.monotonic(), datetime.now()
                if (
                    isinstance(node.value, ast.Name)
                    and node.value.id in clock_modules
                    and node.attr in forbidden_attrs
                ):
                    raise AssertionError(
                        f"sampling_cycle.py must not call {node.value.id}.{node.attr}"
                    )


# ── Construction and lifecycle ────────────────────────────────────────────────


class TestLifecycle:

    def test_disarmed_on_construction(self):
        s = SamplingCycleScheduler()
        assert s.state == CycleState.DISARMED
        assert not s.is_armed

    def test_arm_transitions_to_waiting(self):
        s = SamplingCycleScheduler(period_s=300.0)
        s.arm(t=0.0)
        assert s.state == CycleState.WAITING
        assert s.is_armed

    def test_disarm_returns_to_disarmed(self):
        s = SamplingCycleScheduler(period_s=300.0)
        s.arm(t=0.0)
        s.disarm()
        assert s.state == CycleState.DISARMED
        assert not s.is_armed

    def test_tick_returns_none_when_disarmed(self):
        s = SamplingCycleScheduler(period_s=300.0)
        assert s.tick(t=999.0) is None

    def test_stop_when_waiting_returns_none(self):
        s = SamplingCycleScheduler(period_s=300.0)
        s.arm(t=0.0)
        result = s.stop(t=100.0)
        assert result is None
        assert s.state == CycleState.DISARMED

    def test_stop_when_inserting_returns_retract(self):
        s = SamplingCycleScheduler(period_s=300.0, dwell_s=3.0)
        s.arm(t=0.0)
        r = s.tick(t=300.0)
        assert isinstance(r, CycleInsert)
        assert s.state == CycleState.INSERTING
        retract = s.stop(t=301.0)
        assert isinstance(retract, CycleRetract)
        assert retract.t == pytest.approx(301.0)
        assert s.state == CycleState.DISARMED

    def test_stop_when_disarmed_returns_none(self):
        s = SamplingCycleScheduler()
        assert s.stop(t=0.0) is None

    def test_arm_resets_manual_run_flag(self):
        s = SamplingCycleScheduler(period_s=300.0)
        s.arm(t=0.0)
        s.notify_manual_insert(t=10.0)
        s.disarm()
        s.arm(t=0.0)  # re-arm
        # After re-arm, manual flag should be cleared
        result = s.tick(t=300.0)
        assert isinstance(result, CycleInsert)

    def test_period_and_dwell_properties(self):
        s = SamplingCycleScheduler(period_s=120.0, dwell_s=5.0)
        assert s.period_s == pytest.approx(120.0)
        assert s.dwell_s == pytest.approx(5.0)


# ── Basic tick behaviour ───────────────────────────────────────────────────────


class TestTick:

    def test_insert_fires_at_period_boundary(self):
        s = SamplingCycleScheduler(period_s=300.0, dwell_s=3.0)
        s.arm(t=0.0)
        assert s.tick(t=299.0) is None
        result = s.tick(t=300.0)
        assert isinstance(result, CycleInsert)
        assert result.t == pytest.approx(300.0)
        assert s.state == CycleState.INSERTING

    def test_retract_fires_at_dwell_end(self):
        s = SamplingCycleScheduler(period_s=300.0, dwell_s=3.0)
        s.arm(t=0.0)
        s.tick(t=300.0)  # INSERT
        assert s.tick(t=302.0) is None
        result = s.tick(t=303.0)
        assert isinstance(result, CycleRetract)
        assert result.t == pytest.approx(303.0)
        assert s.state == CycleState.WAITING

    def test_next_insertion_after_retract(self):
        period = 300.0
        dwell = 3.0
        s = SamplingCycleScheduler(period_s=period, dwell_s=dwell)
        s.arm(t=0.0)
        s.tick(t=300.0)  # INSERT at 300
        s.tick(t=303.0)  # RETRACT at 303, next insertion at 300+300=600
        assert s.tick(t=599.0) is None
        result = s.tick(t=600.0)
        assert isinstance(result, CycleInsert)
        assert result.t == pytest.approx(600.0)

    def test_insert_nominal_time_not_tick_time(self):
        """INSERT.t is the period boundary, not the (later) tick timestamp."""
        s = SamplingCycleScheduler(period_s=300.0, dwell_s=3.0)
        s.arm(t=0.0)
        result = s.tick(t=350.0)  # tick arrives 50 s late
        assert isinstance(result, CycleInsert)
        assert result.t == pytest.approx(300.0)  # nominal boundary, not 350


# ── Full 8-hour cycle ──────────────────────────────────────────────────────────


class TestFullEightHourCycle:
    """The eight-hour test must run in milliseconds — no sleeps."""

    def test_insertion_count_and_times(self):
        period = CUP_CYCLE_PERIOD_S  # 300.0 s
        dwell = CUP_CYCLE_DWELL_S   # 3.0 s
        s = SamplingCycleScheduler(period_s=period, dwell_s=dwell)
        s.arm(t=0.0)

        inserts: list[float] = []
        retracts: list[float] = []

        t = 0.0
        end_t = 8.0 * 3600.0  # 28 800 s
        # Step through time at 1-second resolution
        while t <= end_t + dwell + 1.0:
            result = s.tick(t)
            if isinstance(result, CycleInsert):
                inserts.append(result.t)
            elif isinstance(result, CycleRetract):
                retracts.append(result.t)
            t += 1.0

        expected_count = int(end_t / period)  # 96
        assert len(inserts) == expected_count, (
            f"Expected {expected_count} insertions, got {len(inserts)}"
        )
        assert len(retracts) == expected_count

        for i, ins_t in enumerate(inserts):
            expected_t = (i + 1) * period
            assert ins_t == pytest.approx(expected_t), (
                f"Insertion {i + 1}: expected t={expected_t}, got t={ins_t}"
            )

        for i, ret_t in enumerate(retracts):
            # Retract fires at insertion_start + dwell = (i+1)*period + dwell
            expected_t = (i + 1) * period + dwell
            assert ret_t == pytest.approx(expected_t), (
                f"Retract {i + 1}: expected t={expected_t}, got t={ret_t}"
            )

        # Verify the period is measured from insertion start to insertion start,
        # not from retract to next insertion (which would add dwell to every cycle)
        if len(inserts) >= 2:
            for i in range(len(inserts) - 1):
                gap = inserts[i + 1] - inserts[i]
                assert gap == pytest.approx(period), (
                    f"Gap between insertion {i+1} and {i+2}: expected {period}, got {gap}"
                )


# ── Manual insert / retract ────────────────────────────────────────────────────


class TestManualOverride:

    def test_manual_insert_opens_manual_run(self):
        s = SamplingCycleScheduler(period_s=300.0)
        s.arm(t=0.0)
        s.notify_manual_insert(t=150.0)
        assert s.state == CycleState.WAITING  # still armed

    def test_manual_insert_causes_skip_at_period_boundary(self):
        s = SamplingCycleScheduler(period_s=300.0, dwell_s=3.0)
        s.arm(t=0.0)
        s.notify_manual_insert(t=150.0)  # manual run opens

        result = s.tick(t=300.0)
        assert isinstance(result, CycleSkipped)
        assert result.insertion_due_t == pytest.approx(300.0)

    def test_skip_advances_next_boundary(self):
        s = SamplingCycleScheduler(period_s=300.0, dwell_s=3.0)
        s.arm(t=0.0)
        s.notify_manual_insert(t=150.0)
        s.tick(t=300.0)  # CycleSkipped, next boundary → 600

        tti = s.time_to_next_insertion(t=300.0)
        assert tti == pytest.approx(300.0)

    def test_manual_retract_closes_manual_run(self):
        s = SamplingCycleScheduler(period_s=300.0, dwell_s=3.0)
        s.arm(t=0.0)
        s.notify_manual_insert(t=150.0)
        s.tick(t=300.0)  # skip
        s.notify_manual_retract(t=400.0)

        # Next boundary at 600 should fire as INSERT, not skip
        result = s.tick(t=600.0)
        assert isinstance(result, CycleInsert)
        assert result.t == pytest.approx(600.0)

    def test_skip_recorded_following_insertion_not_skipped(self):
        """The insertion that follows a skip is not also skipped."""
        s = SamplingCycleScheduler(period_s=300.0, dwell_s=3.0)
        s.arm(t=0.0)
        s.notify_manual_insert(t=150.0)

        inserts: list[float] = []
        skips: list[float] = []

        for t_int in range(0, 1200):
            t = float(t_int)
            if t == 400.0:
                s.notify_manual_retract(t=400.0)
            result = s.tick(t)
            if isinstance(result, CycleInsert):
                inserts.append(result.t)
            elif isinstance(result, CycleSkipped):
                skips.append(result.insertion_due_t)

        assert len(skips) == 1
        assert skips[0] == pytest.approx(300.0)
        assert len(inserts) >= 1
        assert inserts[0] == pytest.approx(600.0)

    def test_manual_insert_during_inserting_abandons_dwell(self):
        """Operator retraction during a scheduled dwell abandons the dwell."""
        s = SamplingCycleScheduler(period_s=300.0, dwell_s=10.0)
        s.arm(t=0.0)
        r = s.tick(t=300.0)
        assert isinstance(r, CycleInsert)
        assert s.state == CycleState.INSERTING

        # Operator takes over mid-dwell
        s.notify_manual_insert(t=302.0)
        assert s.state == CycleState.WAITING

        # Dwell end at t=310 should NOT fire (dwell was abandoned)
        assert s.tick(t=310.0) is None

        # Next insertion should be at t = 302 + 300 = 602
        s.notify_manual_retract(t=350.0)
        result = s.tick(t=602.0)
        assert isinstance(result, CycleInsert)
        assert result.t == pytest.approx(602.0)

    def test_manual_retract_after_skip_does_not_affect_schedule(self):
        """Retracting after a skip does not reset _next_insertion_t."""
        s = SamplingCycleScheduler(period_s=300.0, dwell_s=3.0)
        s.arm(t=0.0)
        s.notify_manual_insert(t=150.0)
        s.tick(t=300.0)  # skip at 300, next at 300+300=600
        s.notify_manual_retract(t=400.0)

        tti = s.time_to_next_insertion(t=400.0)
        assert tti == pytest.approx(200.0)  # 600 - 400


# ── Period and dwell changes ───────────────────────────────────────────────────


class TestPeriodAndDwellChanges:

    def test_period_change_does_not_affect_current_boundary(self):
        """Changing period while WAITING does not reschedule the pending insertion."""
        s = SamplingCycleScheduler(period_s=300.0, dwell_s=3.0)
        s.arm(t=0.0)
        s.set_period(600.0)

        # First insertion still fires at t=300 (old period boundary)
        result = s.tick(t=300.0)
        assert isinstance(result, CycleInsert)
        assert result.t == pytest.approx(300.0)

    def test_period_change_takes_effect_after_current_boundary(self):
        """After the boundary fires, subsequent insertions use the new period."""
        s = SamplingCycleScheduler(period_s=300.0, dwell_s=3.0)
        s.arm(t=0.0)
        s.set_period(600.0)

        inserts: list[float] = []
        for t_int in range(0, 1500):
            t = float(t_int)
            result = s.tick(t)
            if isinstance(result, CycleInsert):
                inserts.append(result.t)

        # First insertion at 300 (old period, still in effect for this boundary)
        # Second at 300 + 600 = 900 (new period measured from first insertion start)
        assert inserts[0] == pytest.approx(300.0)
        assert inserts[1] == pytest.approx(900.0)

    def test_period_change_mid_inserting_takes_effect_after_retract(self):
        """Period changed during INSERTING applies to the next WAITING period."""
        s = SamplingCycleScheduler(period_s=300.0, dwell_s=3.0)
        s.arm(t=0.0)
        s.tick(t=300.0)  # INSERT at 300, current_insertion_t=300
        s.set_period(600.0)
        s.tick(t=303.0)  # RETRACT at 303; next = current_insertion_t(300) + new_period(600) = 900

        result = s.tick(t=900.0)
        assert isinstance(result, CycleInsert)
        assert result.t == pytest.approx(900.0)

    def test_set_dwell_takes_effect_at_next_insertion(self):
        """set_dwell does not change an in-progress dwell."""
        s = SamplingCycleScheduler(period_s=300.0, dwell_s=10.0)
        s.arm(t=0.0)
        s.tick(t=300.0)   # INSERT — dwell_end = 310
        s.set_dwell(3.0)  # change dwell while in INSERTING

        # Old dwell_end (310) still fires, not the new 303
        assert s.tick(t=305.0) is None
        result = s.tick(t=310.0)
        assert isinstance(result, CycleRetract)
        assert result.t == pytest.approx(310.0)

    def test_set_dwell_used_for_next_insertion(self):
        s = SamplingCycleScheduler(period_s=300.0, dwell_s=10.0)
        s.arm(t=0.0)
        s.tick(t=300.0)  # INSERT at 300 — dwell_end = 310
        s.tick(t=310.0)  # RETRACT at 310, next insertion at 300+300=600
        s.set_dwell(3.0)  # new dwell takes effect at the next insertion

        s.tick(t=600.0)   # INSERT at 600 — dwell_end = 600 + 3 = 603
        result = s.tick(t=603.0)
        assert isinstance(result, CycleRetract)
        assert result.t == pytest.approx(603.0)


# ── time_to_next_insertion / time_to_retract ──────────────────────────────────


class TestTimeQueries:

    def test_time_to_next_insertion_waiting(self):
        s = SamplingCycleScheduler(period_s=300.0)
        s.arm(t=0.0)
        tti = s.time_to_next_insertion(t=100.0)
        assert tti == pytest.approx(200.0)

    def test_time_to_next_insertion_not_negative(self):
        s = SamplingCycleScheduler(period_s=300.0)
        s.arm(t=0.0)
        # Past the boundary — clamped to 0
        tti = s.time_to_next_insertion(t=350.0)
        assert tti == pytest.approx(0.0)

    def test_time_to_next_insertion_none_when_disarmed(self):
        s = SamplingCycleScheduler(period_s=300.0)
        assert s.time_to_next_insertion(t=0.0) is None

    def test_time_to_next_insertion_none_when_inserting(self):
        s = SamplingCycleScheduler(period_s=300.0, dwell_s=3.0)
        s.arm(t=0.0)
        s.tick(t=300.0)  # now INSERTING
        assert s.time_to_next_insertion(t=300.0) is None

    def test_time_to_retract_inserting(self):
        s = SamplingCycleScheduler(period_s=300.0, dwell_s=3.0)
        s.arm(t=0.0)
        s.tick(t=300.0)  # INSERT at 300, dwell_end = 303
        ttr = s.time_to_retract(t=301.0)
        assert ttr == pytest.approx(2.0)

    def test_time_to_retract_none_when_waiting(self):
        s = SamplingCycleScheduler(period_s=300.0)
        s.arm(t=0.0)
        assert s.time_to_retract(t=0.0) is None

    def test_time_to_retract_none_when_disarmed(self):
        s = SamplingCycleScheduler(period_s=300.0)
        assert s.time_to_retract(t=0.0) is None


# ── Multiple boundaries in one call to tick ───────────────────────────────────


class TestMultipleBoundaries:

    def test_two_ticks_for_two_events(self):
        """tick() processes at most one event per call."""
        s = SamplingCycleScheduler(period_s=300.0, dwell_s=3.0)
        s.arm(t=0.0)
        # t=310 is past both the INSERT boundary (300) and the RETRACT boundary (303)
        r1 = s.tick(t=310.0)
        assert isinstance(r1, CycleInsert)  # first event: INSERT at 300
        r2 = s.tick(t=310.0)
        assert isinstance(r2, CycleRetract)  # second event: RETRACT at 303
        r3 = s.tick(t=310.0)
        assert r3 is None  # nothing else due

    def test_consecutive_skips_advance_to_next_boundary(self):
        """Multiple consecutive skips advance the schedule correctly."""
        s = SamplingCycleScheduler(period_s=300.0, dwell_s=3.0)
        s.arm(t=0.0)
        s.notify_manual_insert(t=0.0)  # manual run open immediately

        skips: list[float] = []
        # Tick through t=900; should skip insertions due at 300 and 600
        for t_int in range(0, 901):
            t = float(t_int)
            result = s.tick(t)
            if isinstance(result, CycleSkipped):
                skips.append(result.insertion_due_t)

        assert skips == pytest.approx([300.0, 600.0, 900.0])

    def test_skips_do_not_queue(self):
        """After clearing the manual run, only one insert fires — not catch-up inserts.

        Three boundaries (300, 600, 900) are due while the manual run is open.
        After the manual run closes at t=1000, the scheduler does NOT catch up
        with all three missed insertions. The next boundary is at 1200 (= 900+300),
        and only that one fires.
        """
        s = SamplingCycleScheduler(period_s=300.0, dwell_s=3.0)
        s.arm(t=0.0)
        s.notify_manual_insert(t=0.0)

        # Skip t=300, 600, 900 — all skipped because manual run is open
        for t_int in range(0, 901):
            s.tick(float(t_int))

        # Manual retract at t=1000 — manual run closes
        s.notify_manual_retract(t=1000.0)

        # The scheduler does not catch up: next insertion is at 900+300=1200.
        # No insertion fires between t=1000 and t=1199.
        for t_int in range(1000, 1200):
            result = s.tick(float(t_int))
            assert result is None, f"Unexpected action at t={t_int}: {result}"

        result = s.tick(t=1200.0)
        assert isinstance(result, CycleInsert)
        assert result.t == pytest.approx(1200.0)
