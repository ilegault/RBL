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

---

## Amendment, 2026-09-24: the acquisition settings are operator-editable

Decision 1 above says both thresholds and both intervals are "configuration, not
literals". They were constants in `rbl/config/cup_config.py`, which on the control PC
means they are compiled into the exe: changing one is a code edit and a PyInstaller
rebuild. That is not configuration in any sense useful at the bench, and the first
irradiation with a beam current below the arm threshold silently produces no runs at
all.

This amendment does not change what the detector does or how a run is bounded. It
changes only who can set the numbers, and when.

A1. **Three values are editable from the Faraday Cup tab**: the arm threshold, the
    release threshold, and the per-insertion settle window (ADR 0003, the window
    whose samples are excluded from the post-settle mean and from the dose). The
    sampling cycle's period and dwell were already editable and remain so.

A2. **Two values are editable in the settings file only**: the arm debounce and the
    release interval. They have no field on the tab. They exist in the file so a
    bench session can change them and restart, without a rebuild. The warning in
    `cup_config.py` about not reusing them for the confirmed-position path is
    unchanged and still binding.

A3. **Edited values persist across restarts**, in `~/.config/rbl/cup_settings.json`,
    written after every committed edit. `cup_config.py` keeps the defaults, and a
    "Reset to defaults" control restores them. A value differing from its default is
    shown as such on the tab, so a threshold inherited from last week is visible
    rather than silent.

A4. **The application owns the settings file while it is running.** It rewrites the
    file on every committed edit, so a hand edit made while the application is open
    is overwritten without warning. The file says so in its own `_README` key, and
    the application does not attempt to merge. Hand edits happen with the
    application closed.

A5. **A value rejected at load falls back to its default, visibly.** Every key is
    validated when the file is read. A bad value never enters the application and
    never fails silently: the tab names the key, the value found, the reason, and the
    default used in its place. Valid keys in the same file still load.

A6. **Values that decide what counts as data are locked while data is being
    collected.** The arm threshold, release threshold and settle window cannot be
    edited while an acquisition run is open or while the sampling cycle is armed. The
    fields are disabled, not merely ignored, and the reason is displayed. To change
    one mid-irradiation the operator stops the cycle, edits, and re-arms; a
    "Keep previous schedule" option re-arms onto the boundary the stopped cycle would
    have hit, so no sampling point is lost.

A7. **Period and dwell stay editable while the cycle is armed.** They are schedule
    settings and cannot alter an insertion already recorded. An edit is queued and
    displayed as pending with the time it takes effect, and applies at the next
    period boundary (period) or the next insertion (dwell). Nothing is applied
    mid-insertion.

A8. **Every change is in the record.** Decision 4 above already records the
    thresholds in force at each run. Additionally, a settings-change marker is
    written to the session file for each of the five editable values, carrying the
    old value, the new value and the timestamp; cycle disarm and re-arm markers carry
    the saved boundary and whether the re-arm resumed it or started a fresh
    countdown; and the two file-only values are written into the session header. A
    reader looking at two insertions judged by different thresholds can see exactly
    when and to what the value changed.

### Consequences of the amendment

The cost named in the original Consequences section gets sharper: a threshold set too
high silently truncates the start of every insertion, and now an operator can set it
too high from the tab, mid-session, without touching code. The mitigations are the
lock in A6, the per-run recording in decision 4, and the markers in A8. What the
application can no longer do is quietly disagree with its own archive.

The settle window is different in kind from the thresholds: it does not decide when a
run starts, it decides which of a run's samples reach the mean current and the dose.
Editing it changes a derived number rather than the raw record, and the raw samples
remain in the file, so any settle window can be re-applied later in analysis. It is
locked alongside the thresholds anyway, because an insertion whose samples were
selected by two different rules is not one measurement.
