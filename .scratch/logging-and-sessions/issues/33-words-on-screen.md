# 33: The words on screen say what they do

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 32

**Spec:** `.scratch/logging-and-sessions/spec.md` (section C7)
**Binding:** `CONTEXT.md` ("Logging and sessions", "Known collisions": arm), `docs/adr/0002-cup-acquisition-triggered-by-current.md` (2026-10-05 amendment, B5), `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/gui/faraday_cup_tab.py` (the `Sampling Cycle` group, every
`setText` in `_update_cycle_view`, the `Cannot arm:` text, the threshold labels in the
acquisition settings group cup-settings added), `src/rbl/gui/widgets/cup_panel.py`,
`src/rbl/services/cup_session_writer.py` (`_write_header_comments` and any `details`
strings mentioning arm or release).

## What to build

Text a person reads only. Python identifiers, CSV column names and settings-file JSON keys
keep their names, because analysis scripts read them.

| Before | After |
|---|---|
| Arm Cycle | Start automatic cup insertion |
| Stop Cycle | Stop automatic cup insertion |
| Sampling Cycle (group title) | Automatic cup insertion |
| Cannot arm: <reason> | Cannot start automatic cup insertion: <reason> |
| Arm threshold (label, tooltip) | Run start current |
| Release threshold (label, tooltip) | Run end current |
| Armed / Disarmed (state label) | Running / Stopped |

Also the cup log's `#` header comment text and marker `details` text.

Tests may fake: nothing; build the real tab. Existing tests asserting the old strings are
rewritten in place under the same names to assert the new strings.

## Acceptance criteria

- [ ] A test collects the text of every `QPushButton`, `QLabel`, `QGroupBox` title and
      tooltip in `FaradayCupTab` and `CupPanel` and asserts none matches the regex
      `\b(arm|armed|disarm|disarmed|release threshold)\b` (case-insensitive).
- [ ] The table's six new strings each appear: a test asserts each by its widget.
- [ ] A cup log header written after this ticket contains `run start current` and
      `run end current` and not `arm threshold`; its CSV column names are unchanged
      (compare to `CSV_COLUMNS`).
- [ ] `grep -rn "def .*arm" src/rbl/services/sampling_cycle.py` still finds `arm` and
      `disarm`: identifiers are untouched.
- [ ] The tab's module docstring uses the new words and notes the identifier mapping.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile --durations=25

## Comments
