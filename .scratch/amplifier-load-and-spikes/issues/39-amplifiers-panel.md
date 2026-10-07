# 39: The Amplifiers panel: record an amplifier swap or a hardware change

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 35, 38

**Spec:** `.scratch/amplifier-load-and-spikes/spec.md` (Part A)
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`, `CONTEXT.md` (Amplifier assignment, Amplifier swap, Hardware change)

**Read first:** `src/rbl/gui/load_characterization_tab.py` (after 38), `src/rbl/config/amplifier_assignments.py` (from 35), `src/rbl/gui/faraday_cup_tab.py` (`_confirm_stop_automatic` - copy how a dialog sits behind a replaceable method so tests never open a modal).

## What to build

A group box "Amplifiers" on the Load Characterization tab: four rows (plate position, the serial
currently assigned or `not set`) and two buttons:

- **Record amplifier swap** calls `self._ask_assignment(current_mapping) -> (mapping, when, note) | None`
  (the real implementation is a dialog with one serial field per plate position, a date
  defaulting to now, and a note). On a result it calls `record_assignment` with kind `initial`
  if no assignment exists yet, otherwise `swap`, then refreshes the rows and the results table.
- **Record hardware change** calls `self._ask_hardware_change() -> (when, note) | None`, then
  `record_hardware_change`, then refreshes the results table.

Tests replace `_ask_assignment` and `_ask_hardware_change`.

Tests may fake: the two `_ask_*` methods. The assignment store is real (temp path).

## Acceptance criteria

- [x] With no assignment, the rows read `not set`; after a faked `_ask_assignment` returns four serials, the rows show them and the store holds one record with kind `initial`.
- [x] A second faked swap writes a record with kind `swap` and the rows show the new mapping.
- [x] A faked `_ask_assignment` returning `None` writes nothing.
- [x] After Record hardware change, a previously normal result cell has background `theme.WARN` without rebuilding the tab.
- [x] With `QDialog.exec` replaced by a function that fails the test, all of the above pass.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile --durations=25

## Comments

2026-10-07: Done. The Load Characterization tab has an "Amplifiers" group box: four rows (`not set` or the serial in force) and the buttons "Record amplifier swap" and "Record hardware change". The dialogs (`AssignmentDialog`, `HardwareChangeDialog`) sit behind `_ask_assignment` / `_ask_hardware_change`; the first recording is `initial`, later ones `swap`. Both actions refresh the rows and the results table and emit `measurements_changed` so the Raster Planner re-reads. Tests (`TestAmplifiersPanel`, `TestTheDialogsThemselves`) replace the two ask methods and make any `QDialog.exec` fail the test; the real dialogs are built and read but never executed.
Choices the ticket left open: the assignment dialog enables OK only for four non-empty, distinct serials (one amplifier cannot drive two plates); the hardware-change dialog requires a note; a mapping the store refuses is reported with a warning and nothing is written.
