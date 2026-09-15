# 09: Faraday Cup tab, finished

**What to build:** The Faraday Cup tab becomes the screen an operator actually uses
during an insertion: a live plot of cup current over time, how long the current run has
been going and how many samples it holds, and the running average that the measurement is
for.

**Blocked by:** 08

**Status:** done

The running average must be computed **from the samples that were logged**, not from a
parallel accumulator. Two numbers derived independently are two numbers that can
disagree, and the one on the screen is the one an operator will write in a notebook.

- [x] A live plot shows cup current over time
- [x] Plot navigation follows the pattern the slit current tab already uses, so the two
      screens behave the same way
- [x] The active run's duration and sample count are shown
- [x] A running average of cup current across the active run is shown
- [x] That average is computed from the logged samples, and a test proves the displayed
      value and the file agree
- [x] Over-range samples are excluded from the average, and their exclusion is visible
      rather than silent
- [x] With nothing connected the tab is clean: no stale plot, no zeroed average, an
      explicit disconnected state
- [x] A disconnection mid-run leaves the tab in an honest state rather than a frozen one
- [x] Numeric entry uses the project's own input widgets, not bare spin boxes
- [x] Colours come from theme roles, not hex codes
- [x] Full suite green

## Comments

### 2026-09-15
- Implemented live scrolling plot with historical navigation on Faraday Cup tab (`src/rbl/gui/faraday_cup_tab.py`) using `LivePlotPanel` and `RollingBuffer(36_000)` at 10 Hz matching `SlitCurrentsTab` navigation idioms (LIVE mode, FROZEN mode, zoom in/out, jump to live, history slider).
- Implemented active run metrics computed strictly from logged samples in `CupSessionWriter` (`src/rbl/services/cup_session_writer.py`):
  - `CupRunStats` dataclass tracking `duration_s`, `total_samples`, `valid_samples`, `over_range_samples`, and `average_current_a`.
  - Display of active run duration, sample count, running average, and explicit over-range sample exclusion (`N valid samples (M over-range excluded)`).
- Clean disconnected lifecycle: resets all metrics to honest placeholders (`—`), clears live plot trace, stops redraw timer on hide/disconnect, cleanly closes active runs and logs markers on disconnection mid-run.
- Added comprehensive unit and headless GUI integration tests in `tests/test_cup_session_writer.py` and `tests/test_faraday_cup_tab.py`:
  - Verified active run duration, sample counts, and running average computed directly from session writer samples.
  - Verified displayed running average mathematically agrees with the average calculated from the generated session CSV file.
  - Verified over-range exclusion from average and visible UI flagging.
  - Verified plot navigation, zoom in/out, jump to live, and show/hide redraw timer control.
- All gates passed:
  - `ruff check .` clean
  - `check_tests_first.py` passed
  - `type_gate.py` passed (0 hard-layer errors, 139/139 soft-layer errors)
  - `pytest` 100% green (1,732 passed, 0 failed).

