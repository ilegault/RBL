# 08: Session-file rows for cycle disarm and re-arm

**Blocked by:** 05

**Status:** ready-for-agent

**Read first:** `src/rbl/services/cup_session_writer.py`, the `write_*` family.
`src/rbl/services/sampling_cycle.py`, `disarm()` and `stop(t)` - note that they are
two different exits and that only `stop` returns a `CycleRetract`.
`docs/adr/0002-...md` amendment decision A8.

**What to build:** Two marker rows recording the sampling cycle leaving and entering
the armed state, carrying enough for a reader to explain a gap in the insertion
series. A cycle stopped for four minutes while an operator edited a threshold is a
gap; so is a crash. These rows are what tells them apart.

- [ ] `CupSessionWriter.write_cycle_disarmed(t_host: float, reason: str,
      saved_boundary_t: float)` writes one row in the style of the existing `write_*`
      family. `reason` is restricted to exactly `"stop"` or `"disarm"`; any other
      value raises `ValueError` naming it. A test asserts both accepted values write a
      row and a third raises.
- [ ] `CupSessionWriter.write_cycle_armed(t_host: float, mode: str,
      next_insertion_t: float)` writes one row. `mode` is restricted to exactly
      `"fresh"` or `"resumed"`; any other value raises `ValueError`. A test asserts
      both accepted values write a row carrying the next insertion time, and that a
      third raises.
- [ ] Both rows carry their boundary timestamp in the same column format the rest of
      the file uses for times, so a reader parses them with the code it already has. A
      test asserts the value round-trips: write `saved_boundary_t = 1234.5`, read the
      row back, parse it, assert `1234.5`.
- [ ] A test writes the realistic sequence - armed fresh, disarmed with reason
      `"stop"` and a saved boundary, armed again with mode `"resumed"` and that same
      boundary - and asserts the four rows appear in that order in the file with
      matching boundary values. This is the sequence an operator produces when they
      stop the cycle to edit a threshold.
- [ ] Neither method touches run state. A test asserts writing both rows during an
      open run leaves `run_count` and the run's sample count unchanged.

**Tests may fake:** the output directory, via `tmp_path`. The rows must be really
written and really parsed back.

**Out of scope:** the scheduler changes that produce these values (tickets 09 and 10)
and the tab wiring that calls these methods (ticket 16).
