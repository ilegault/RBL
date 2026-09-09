# 09: Faraday Cup tab, finished

**What to build:** The Faraday Cup tab becomes the screen an operator actually uses
during an insertion: a live plot of cup current over time, how long the current run has
been going and how many samples it holds, and the running average that the measurement is
for.

**Blocked by:** 08

**Status:** ready-for-agent

The running average must be computed **from the samples that were logged**, not from a
parallel accumulator. Two numbers derived independently are two numbers that can
disagree, and the one on the screen is the one an operator will write in a notebook.

- [ ] A live plot shows cup current over time
- [ ] Plot navigation follows the pattern the slit current tab already uses, so the two
      screens behave the same way
- [ ] The active run's duration and sample count are shown
- [ ] A running average of cup current across the active run is shown
- [ ] That average is computed from the logged samples, and a test proves the displayed
      value and the file agree
- [ ] Over-range samples are excluded from the average, and their exclusion is visible
      rather than silent
- [ ] With nothing connected the tab is clean: no stale plot, no zeroed average, an
      explicit disconnected state
- [ ] A disconnection mid-run leaves the tab in an honest state rather than a frozen one
- [ ] Numeric entry uses the project's own input widgets, not bare spin boxes
- [ ] Colours come from theme roles, not hex codes
- [ ] Full suite green
