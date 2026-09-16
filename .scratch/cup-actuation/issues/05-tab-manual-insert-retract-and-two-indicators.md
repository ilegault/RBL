# 05: Manual insert and retract, with commanded and confirmed shown apart

**What to build:** An operator presses Insert on the Faraday Cup tab, the cup moves, and
a moment later the confirmed indicator catches up. Press Retract and it goes back. If a
commanded move never confirms, the tab says so loudly. If the controller is not in AUTO,
the tab says that too, so the operator understands why nothing is happening.

This is the first slice an operator can actually use, and manual control remains
available at all times for the rest of this effort — the application never stands between
the operator and the cup.

**Blocked by:** 04

**Status:** ready-for-agent

**Read `docs/adr/0003-commanded-and-confirmed-cup-position.md` first**, decisions 2 and
3. `docs/adr/0001-tests-first-and-no-muted-failures.md` is binding.

## Structural requirements

- **Two indicators, never one.** Commanded and confirmed are shown as two distinct
  things. A cup that was told to move and did not is the specific failure this whole
  feature exists to make visible, and a single combined indicator hides exactly that.
  Use `CONTEXT.md`'s vocabulary on the labels: commanded position, confirmed position,
  in transit, indeterminate, AUTO mode.
- **The tab owns no driver and converts nothing.** It renders the snapshot from
  ticket 04 and emits intent. Cross-tab wiring, if any is needed, goes in
  `MainWindow.__init__` and carries a comment saying why.
- **Faults use theme roles from `config/theme.py`**, never hex codes.
- A commanded move that does not confirm within `2.0` s (the `cup_config.py` timeout) is
  a **fault, not a retry**. It is raised visibly and it does not re-command. Retrying a
  move that the controller is ignoring produces an application that looks busy for hours
  while the cup never moves.

## Skills

Invoke `/design-taste-frontend` before laying out the new panel. This is UI work on a
tab an operator reads under time pressure.

- [ ] Insert and Retract buttons on the Faraday Cup tab, enabled whenever the LabJack is
      connected and disabled when it is not
- [ ] A commanded-position indicator and a separate confirmed-position indicator, both
      always visible, never merged
- [ ] The confirmed indicator distinguishes IN, OUT, in transit, and indeterminate as
      four separate readings
- [ ] An indeterminate reading is presented as a fault, not as a position
- [ ] The tab states plainly when the controller is not in AUTO, and explains in that
      state that remote commands will be accepted and ignored
- [ ] The tab states plainly when the status reading is stale, distinguishing it from
      disconnected
- [ ] A commanded move that does not confirm within the timeout raises a visible fault
      naming which move failed; it does not re-command
- [ ] Every fault and status colour comes from a `config/theme.py` role
- [ ] The tab works with nothing connected, showing a disconnected state rather than
      blank or stale numbers
- [ ] Tests drive the real `Beamline` through ticket 04's feed and assert on what the tab
      renders, not on private widget attributes: a confirming move updates both
      indicators; a non-confirming move leaves confirmed unchanged and raises the
      timeout fault; a not-in-AUTO word produces the AUTO warning; an indeterminate word
      produces the fault state
- [ ] A test asserts that pressing Insert while a move is already in flight does not
      queue a second command
- [ ] No test in this ticket sleeps — the timeout is driven by injected timestamps
- [ ] `ruff check .`, `python scripts/check_tests_first.py`, `python tools/type_gate.py`
      and `pytest --tb=short -q -n auto --dist loadfile` all pass
