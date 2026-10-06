# 15: Period and dwell get rules instead of arbitrary limits, and show what is pending

**Blocked by:** 09, 14

**Status:** blocked

**Read first:** `src/rbl/gui/faraday_cup_tab.py`, the Sampling Cycle panel: the
`self.spn_cycle_period.setRange(10.0, 86400.0)` and
`self.spn_cycle_dwell.setRange(1.0, 60.0)` calls, `_on_cycle_period_changed`,
`_on_cycle_dwell_changed` and `_update_cycle_view`. `docs/adr/0002-...md` amendment
decision A7. `CONTEXT.md`, the glossary entry "Pending change".

**What to build:** The 60-second dwell ceiling and the 86 400-second period ceiling
are replaced by rules that relate the two fields and the settle window to each other,
and the operator can see when a change they typed takes effect. Neither field ever
locks; an edit made while the cycle is armed is queued, displayed, and applied at the
next boundary.

- [x] The two `setRange` calls are replaced by wide absolute bounds - `0.1` to
      `604800.0` on both spin boxes - with the real rules enforced by
      `validate_settings` from ticket 02 on every committed edit. A test asserts the
      spin box maximums and that entering a dwell of 400 with a period of 300 is
      refused, because dwell plus two move-confirmation timeouts must fit inside the
      period.
- [x] A refused edit restores the previous value with `sync_value` and shows the
      warning's `reason` in the `FAULT` role, exactly as the Acquisition Settings
      group does. A test asserts the field value is unchanged after a refusal and that
      the reason is visible. Nothing typed is ever discarded without explanation.
- [x] An accepted edit calls `set_period` or `set_dwell` on the scheduler (which
      queues it, per ticket 09) and calls
      `self.session_writer.write_settings_changed` once with key `cycle_period_s` or
      `cycle_dwell_s` and the old and new values. A test asserts one row per accepted
      edit.
- [x] While the scheduler reports a pending value, a label in the `MUTED` role shows
      it with the time it applies, for example `Dwell 20.0 s pending, applies at next
      insertion` and `Period 600 s pending, applies at next period boundary`. The
      label is rendered from `pending_period_s` and `pending_dwell_s`, **not** from
      what the operator typed, so it states what the scheduler will do. It clears when
      the scheduler clears the pending value. A test asserts the text appears after an
      edit while armed and is gone after the boundary tick that applies it.
- [x] A permanent label in the `MUTED` role under the period and dwell row reads
      exactly `Changes apply from the next insertion.` A test asserts the exact string
      is present whether or not the cycle is armed.

**Tests may fake:** the hardware, via `tests/payloads.py`, and the output directory.
The scheduler, its pending state and the written rows must be real.

**Out of scope:** the "Keep previous schedule" checkbox (ticket 16), and locking these
two fields - they are never locked.

## Comments

## Escalation — 2026-10-06
Repo: RBL   Ticket: 15 Period and dwell get rules instead of arbitrary limits, and show what is pending   Branch: ticket/cup-settings-15-period-and-dwell-rules-and-pending
Goal: The 60-second dwell ceiling and the 86 400-second period ceiling are replaced by rules that relate the two fields and the settle window to each other, and the operator can see when a change they typed takes effect.
Attempt 1: Implemented wide spin box bounds, validate_settings check, sync_value on refusal, lbl_cycle_warning in FAULT role, settings_changed emission, lbl_cycle_permanent note, and lbl_cycle_pending label. Added TestCyclePeriodAndDwellRulesAndPending in tests/test_faraday_cup_tab.py. → All 6 new unit tests pass and all 79 tests in tests/test_faraday_cup_tab.py pass. Ruff, check_tests_first, and type_gate all pass with 0 errors.
Attempt 2: Ran full suite pytest with CI flags (-n auto --dist loadfile). → 2063 tests pass, but 32 test errors occur exclusively in tests/test_log_rollover.py and tests/test_vacuum_monitor_log.py with `ZoneInfoNotFoundError: 'No time zone found with key America/Chicago'`.
Attempt 3: Investigated the failure; commit a960d5e (merged Ticket 19) added `tzdata>=2024.1` to `requirements-dev.txt`, but `tzdata` is not installed in the shared `.venv` on this machine.
Failing output (exact, trimmed to the relevant lines):
```
tests\test_vacuum_monitor_log.py:21: in <module>
    CHICAGO = ZoneInfo("America/Chicago")
zoneinfo._common.ZoneInfoNotFoundError: 'No time zone found with key America/Chicago'
ModuleNotFoundError: No module named 'tzdata'
```
Decision needed: Parallel agents are forbidden from running `pip install` into the shared `.venv`. The developer must install `tzdata` into `.venv` (`pip install tzdata` or `pip install -r requirements-dev.txt`) on the host PC so that zoneinfo finds America/Chicago on Windows during full-suite test runs.
