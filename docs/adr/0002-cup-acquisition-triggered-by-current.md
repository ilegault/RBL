# ADR 0002 — Faraday cup acquisition is triggered by the cup current itself

- **Status:** Accepted
- **Date:** 2026-09-09

## Context

The Faraday cup is inserted into the beam by hand, roughly every five minutes,
and reports no position. Nothing in the beamline can tell the application
whether the cup is in the beam; the only evidence available is the current the
picoammeter reads.

An operator standing at the cup has both hands occupied and is not also at the
keyboard. Requiring a button press to start and stop each recording means that
the recording either misses the beginning of every insertion or does not happen
at all, and the value of this measurement is dose — the integral of current over
the whole insertion, with nothing clipped off the front.

Three approaches were available.

**Press record by hand.** Correct by construction: the application records
exactly what it was told to record and claims nothing. It also loses the first
seconds of every insertion, and loses whole insertions on a busy shift. The
measurement it produces is honest and incomplete.

**Fit a position sensor.** The right answer, and not available. The cup has no
sensor and no actuator today. A later revision is expected to drive insertion
through the LabJack, at which point a commanded-and-confirmed position exists.

**Infer insertion from the current.** The cup reads near zero out of the beam
and microamps in it, so the signal is unambiguous in the ordinary case. This
records the whole insertion and needs no operator action — at the price of the
application deciding, on its own, which samples are data.

## Decision

Acquisition is triggered by the cup current, with hysteresis and a manual
override.

1. A run starts when the cup current stays above the **arm threshold** for the
   debounce interval, and ends when it stays below the **release threshold** for
   the release interval. The release threshold is lower than the arm threshold.
   Both thresholds and both intervals are configuration, not literals.

2. **Only samples inside a run are data.** Idle pings are not written as rows.

3. The application's watching is nonetheless recorded. The session file carries
   markers for state changes and a periodic idle heartbeat, so a gap in the
   record distinguishes "the cup was out and we were watching" from "the
   application was not running" from "the instrument was disconnected". A silent
   gap is not allowed to mean three different things.

4. Every run records the thresholds in force when it began. A later reader can
   see why the application thought a run started.

5. A manual **force start / force stop** is always available and overrides the
   detector completely.

6. The run logic reads a single "is the cup in the beam?" value. Today that
   value is inferred from current. When an actuator and position feedback exist,
   that value is replaced at its source and the run logic does not change.

## Consequences

The application's judgement about what counts as data is baked into the archive.
A threshold set too high silently truncates the start of every insertion, and
nothing downstream can recover what was never written — this is the real cost,
and it is why the thresholds are recorded per run and why the manual override
exists.

Runs are recorded that nobody asked for: any current excursion past the arm
threshold produces one, whether or not the cup was in the beam. Extra runs are
recoverable in analysis, whereas a missed insertion is not, so the design is
biased toward recording.

The current-based inference is the weakest part of this and is expected to be
replaced rather than tuned. Decision 6 is what keeps that replacement cheap.
