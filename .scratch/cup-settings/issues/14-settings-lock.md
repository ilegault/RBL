# 14: The three data-defining fields lock while data is being collected

**Blocked by:** 13

**Status:** done

**Read first:** `docs/adr/0002-...md` amendment decision A6. `CONTEXT.md`, the
glossary entry "Settings lock". `src/rbl/gui/faraday_cup_tab.py` -
`self.acquisition.is_acquiring`, `self.cycle.is_armed`, and `_update_cycle_view`,
which already runs on every actuation state update and is the natural place to
recompute this.

**What to build:** The arm threshold, release threshold and settle window cannot be
edited while an acquisition run is open or while the sampling cycle is armed, and the
reason is on screen. These three decide what counts as data; changing one partway
through an insertion would mean one measurement judged by two rules. Period and dwell
are schedule settings and are never locked.

- [x] The tab computes the lock as `self.acquisition.is_acquiring or
      self.cycle.is_armed` and calls `AcquisitionSettingsGroup.set_locked` with it.
      The computation happens on every actuation state update and on every run open
      and run close, so the fields unlock the moment a run ends. A test asserts
      `set_locked(True)` reached the widget in each of the three locking situations:
      run open with cycle disarmed, cycle armed with no run, and both.
- [x] With neither condition true, the three fields report `isEnabled() is True`. A
      test asserts this after a run closes, proving the lock releases rather than
      latching.
- [x] The Sampling Cycle panel's period and dwell spin boxes report
      `isEnabled() is True` in all four combinations of run-open and cycle-armed. A
      test asserts all four. Locking them would leave an operator unable to re-tune
      the sampling rate for eight hours.
- [x] The lock line text is exactly `Locked while a run is open or the cycle is
      armed. Edits apply to the next run.`, shown only while locked. A test asserts
      the exact string and its visibility in both states.
- [x] One test drives the real pipeline rather than poking flags: using
      `tests/payloads.py`, feed a current that opens a run through a real `Beamline`
      and assert the fields disable; feed a current that closes it and assert they
      enable. A lock that only works when a test sets `is_acquiring` by hand is not a
      lock.

**Tests may fake:** the hardware, via `tests/payloads.py`, and the output directory.
The state machine, the scheduler state and the widget's enabled state must be real.

**Out of scope:** period and dwell behaviour beyond asserting they stay enabled
(ticket 15), and the resume checkbox (ticket 16).

## Comments

### Completed 2026-10-06
- Implemented `_update_settings_lock` on `FaradayCupTab`, computing `locked = bool(self.acquisition.is_acquiring or self.cycle.is_armed)` and calling `self.settings_group.set_locked(locked)`.
- Invoked `_update_settings_lock()` on initialization, during `_update_acquisition_view()` (on all run open/close and force start/stop actions), on run transitions in `on_cup_state`, on actuation state updates in `on_cup_actuation_state`, when actuation disconnects in `_set_actuation_disconnected_view`, and on cycle view updates in `_update_cycle_view()`.
- Added `TestFaradayCupTabSettingsLock` in `tests/test_faraday_cup_tab.py` covering:
  - `test_set_locked_called_in_all_three_locking_situations` (run open / cycle armed / both)
  - `test_lock_releases_after_run_closes_and_fields_enabled`
  - `test_cycle_period_and_dwell_remain_enabled_in_all_four_combinations`
  - `test_lock_label_text_and_visibility`
  - `test_e2e_pipeline_current_locks_and_unlocks_settings` driving beamline stream currents via `CupFeed`
- Zero private access ratchet violations (maintained count 462).
- Bench verification: no physical bench required beyond unit/pipeline tests.
