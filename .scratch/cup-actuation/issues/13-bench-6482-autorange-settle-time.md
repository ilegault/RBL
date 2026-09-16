# 13: Bench — measure the 6482's autorange settle time

**What to build:** Nothing. This measures a number the spec currently guesses at.

**Blocked by:** None (can start immediately)

**Status:** ready-for-developer

**An agent must not claim this ticket.**

## The number and why it matters

The Faraday cup reader commits to autoranging always enabled. At a three-second dwell and
10 Hz that is thirty readings, and nobody knows how many of them are spent range-hunting
on the step from baseline into the beam.

The measured answer sets two things: the **settle window** (ticket 08's placeholder
`1.0` s, whose docstring says it is a placeholder) and the **minimum useful dwell**. A
dwell shorter than the settle time produces an insertion whose entire sample set is
excluded from its own mean.

The reader already works, so this can be measured today with the cup inserted by hand.

## If the answer is bad

If autoranging cannot settle inside a usable dwell, the fix is a **fixed range during
commanded insertions**. That would amend the reader spec's autorange decision and **needs
recording in the ADR** rather than being done quietly — a fixed range that is wrong
produces a clipped or noise-floor reading that looks like a measurement.

- [ ] A step from out-of-beam baseline into the beam is captured at 10 Hz with
      autoranging enabled, several times
- [ ] The number of readings spent range-hunting is counted, not estimated
- [ ] A recommended settle window and a minimum useful dwell are stated as numbers
- [ ] If the settle time exceeds a usable dwell, an ADR amendment is drafted rather than
      a fixed range being set quietly
- [ ] Findings recorded under this ticket's `## Comments`, with the measured values, so
      `CUP_SETTLE_WINDOW_S` can be set from evidence
