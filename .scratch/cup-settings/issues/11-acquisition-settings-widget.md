# 11: The Acquisition Settings group, as a standalone widget

**Blocked by:** 02

**Status:** done

**Read first:** `src/rbl/gui/widgets/inputs.py` - `QuietDoubleSpinBox`,
`ScientificDoubleSpinBox`, `sync_value` and `unit_row`, including the module docstring
explaining why a bare `QDoubleSpinBox` is never used and why a unit never goes inside
the box. `src/rbl/gui/theme.py` for the `MUTED` and `FAULT` roles and
`status_label`. `src/rbl/gui/faraday_cup_tab.py`, the Sampling Cycle panel
construction, for the layout idiom to match.

**What to build:** The group box the operator types into: arm threshold, release
threshold, settle window. It validates, it refuses bad values visibly, it announces
changes by signal, and it can be locked. It is a standalone widget file that owns no
driver, no detector, no scheduler and no file - it is handed values and reports edits,
and ticket 12 connects it to the rest.

- [x] `src/rbl/gui/widgets/acquisition_settings.py` (new) defines
      `AcquisitionSettingsGroup(QGroupBox)` titled `Acquisition Settings`, holding
      `spn_arm_threshold` and `spn_release_threshold` as `ScientificDoubleSpinBox` in
      **amps** and `spn_settle_window` as `QuietDoubleSpinBox` in **seconds**, each in
      a `unit_row` with unit text `A`, `A` and `s`. A test asserts the three widget
      types and that no bare `QDoubleSpinBox` appears in the file. Thresholds are in
      amps because that is how they are stored, compared and recorded: no microamp
      conversion exists anywhere in this feature.
- [x] `set_values(settings: CupSettings)` fills the three boxes using `sync_value` so
      no edit signal is emitted, and `values()` returns the three current numbers. A
      test asserts a `set_values` call emits nothing and that `values()` round-trips.
- [x] A committed edit is validated through `validate_settings` from ticket 02, using
      the widget's other current values for the cross-field rules. A valid edit emits
      `settings_changed(str, float, float)` exactly once with the key, the old value
      and the new value. A rejected edit emits nothing, restores the previous value
      with `sync_value`, and shows the warning's `reason` in a label styled with the
      `FAULT` role. An offscreen test asserts both paths, including that setting the
      release threshold above the arm threshold reverts and shows a reason.
- [x] `set_locked(locked: bool)` disables all three spin boxes with
      `setEnabled(False)` and shows a label styled with the `MUTED` role reading
      exactly: `Locked while a run is open or the cycle is armed. Edits apply to the
      next run.` Unlocking re-enables them and hides the label. A test asserts
      `isEnabled()` for all three in both states and asserts the exact label text. A
      field that accepted typing and discarded it silently is the failure this
      prevents.
- [x] `set_load_warnings(warnings: list[SettingsLoadWarning])` renders one line per
      warning naming the key, the value found, the reason and the fallback used, in
      the `FAULT` role; an empty list hides it. A `btn_reset_defaults` button emits
      `settings_changed` once per field that differs from its default, with the
      default as the new value. Tests assert the warning text contains all four parts
      and that resetting two changed fields emits exactly two signals.

**Tests may fake:** nothing beyond Qt's offscreen platform, which `tests/conftest.py`
already sets. No detector, no writer, no file is involved in this widget at all.

**Out of scope:** connecting any of this to the tab (ticket 12), persistence
(ticket 13), deciding when to lock (ticket 14), and the period and dwell fields, which
stay in the Sampling Cycle panel.

## Comments

### Landed 2026-10-05
- Created `AcquisitionSettingsGroup(QGroupBox)` in `src/rbl/gui/widgets/acquisition_settings.py` holding `spn_arm_threshold` (amps), `spn_release_threshold` (amps), `spn_settle_window` (seconds), and `btn_reset_defaults`.
- Implemented `set_values`, `values` roundtrip, validation of committed edits via `validate_settings`, and `settings_changed` signal emission.
- Implemented `set_locked` showing exact locked message in MUTED role and disabling spin boxes, and `set_load_warnings` rendering in FAULT role.
- Added comprehensive unit tests in `tests/test_acquisition_settings.py` covering all acceptance criteria.
