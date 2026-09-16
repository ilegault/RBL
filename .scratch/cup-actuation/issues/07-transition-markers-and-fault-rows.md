# 07: Position transitions and faults in the record

**What to build:** Every confirmed cup movement and every fault lands in the session file
as it happens, so a specimen's interrupted exposure is reconstructible afterwards and a
fault that occurred at 3 a.m. during an unattended run is still there in the morning.

**Blocked by:** 05, 06

**Status:** ready-for-agent

**Read `docs/adr/0003-commanded-and-confirmed-cup-position.md` first.**
`docs/adr/0001-tests-first-and-no-muted-failures.md` is binding.

## Structural requirements

- `cup_session_writer.py` today writes sample rows, lifecycle markers, per-run sample
  counts and session metadata. This ticket extends that schema; it does not fork it.
  Every new row goes through the existing `_write_row` path so it inherits the
  flush-after-every-row behaviour, and for the same reason: an eight-hour run that dies
  at hour seven must leave seven hours of usable record.
- **The logging is consistent or it is useless.** A transition marker's timestamp and a
  sample row's timestamp must be the same clock and the same format, so a reader can
  interleave them without guessing. State that in the module docstring.
- One `record_type` value per new row kind, added to the existing vocabulary rather than
  overloading `details`.

## The rows

**Position transition** — every confirmed IN and every confirmed OUT, carrying the
confirmed timestamp and the position transitioned to.

**Fault rows**, one `record_type` each:

- move commanded but not confirmed within the timeout, naming which move
- controller not in AUTO
- impossible status combination (both contacts asserted)
- position-versus-current disagreement, carrying both readings

- [ ] `CSV_COLUMNS` is extended, not replaced; existing columns keep their meaning and
      position
- [ ] Every confirmed IN and OUT writes a transition row with its confirmed timestamp
- [ ] Each of the four fault kinds writes a distinctly typed row carrying enough detail
      to diagnose it without the application running
- [ ] A disagreement row carries the confirmed position and the measured current that
      disagreed with it, both, in the same row
- [ ] Every new row flushes immediately, like the existing ones
- [ ] The CSV comment header describes the new row kinds
- [ ] A test writes a transition and a sample in the same session and asserts their
      timestamps are the same clock and the same format
- [ ] A test asserts the file survives a simulated mid-run abort with every row written
      before the abort intact and parseable
- [ ] A test reads back a file containing all four fault kinds and asserts each is
      distinguishable by `record_type` alone
- [ ] The module docstring's `WHY THIS EXISTS` is updated: it currently says the cup is
      inserted manually and carries no position sensor, which is no longer true
- [ ] `ruff check .`, `python scripts/check_tests_first.py`, `python tools/type_gate.py`
      and `pytest --tb=short -q -n auto --dist loadfile` all pass
