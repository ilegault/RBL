# 07: Threshold-triggered acquisition runs, with manual override

**What to build:** The application notices when the cup goes into the beam and starts an
acquisition run on its own; it notices when the cup comes out and ends the run. The tab
shows plainly whether a run is active. An operator can force a run to start or stop at
any time, overriding the detector completely.

Nothing is written to disk in this ticket. What this delivers is: insert the cup, watch
the tab say it is acquiring; withdraw it, watch the run close.

**Blocked by:** 06

**Status:** ready-for-agent

**Read `docs/adr/0002-cup-acquisition-triggered-by-current.md` first.** It records why
insertion is inferred rather than commanded, and decision 6 constrains how this is built.

Two structural requirements:

- The state machine is a **pure object in the services layer**. It does not import Qt and
  it does not call the clock. Timestamps are inputs. This is what makes the debounce and
  release intervals testable exactly rather than by sleeping.
- It exposes **one value** answering "is the cup in the beam?", derived today from
  current. A future commanded-and-confirmed cup position must be able to replace that
  value at its source without the run logic changing. Do not scatter threshold
  comparisons through the run logic.

- [ ] A run opens when cup current stays above the arm threshold for the debounce interval
- [ ] A run closes when cup current stays below the release threshold for the release interval
- [ ] The release threshold is lower than the arm threshold
- [ ] Thresholds and intervals live in the config layer; starting values are 0.5 µA,
      0.25 µA, 1.0 s and 3.0 s
- [ ] Polling moves to 10 Hz while a run is active and back to 2 Hz when it closes
- [ ] The tab shows whether a run is active
- [ ] Force start opens a run regardless of current; force stop closes one regardless
- [ ] A disconnection mid-run closes the run rather than leaving it open indefinitely
- [ ] The state machine is tested directly with a synthetic current-versus-time series
- [ ] Test cases include: a clean insertion and withdrawal; a spike shorter than the
      debounce, which must not open a run; a current dwelling between the two thresholds,
      which must not close and reopen a run; a withdrawal shorter than the release
      interval, which must not close the run; force start below threshold; force stop
      above it; a disconnection mid-run
- [ ] No test in this ticket sleeps
