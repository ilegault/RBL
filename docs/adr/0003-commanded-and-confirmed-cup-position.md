# ADR 0003 — Cup position is commanded and confirmed, and the cup fails into the beam

- **Status:** Accepted
- **Date:** 2026-09-15
- **Supersedes:** ADR 0002 decision 6. The rest of ADR 0002 stands.

## Context

ADR 0002 decided that acquisition is triggered by the cup current itself, because the
cup was moved by hand and reported no position. Its decision 6 anticipated this
moment:

> The run logic reads a single "is the cup in the beam?" value. Today that value is
> inferred from current. When an actuator and position feedback exist, that value is
> replaced at its source and the run logic does not change.

The actuator and the position feedback now exist. The Faraday Cup Controller has an
ESM remote connector that accepts dry-contact closures to enable and command cup-out,
and returns isolated contact closures reporting cup IN, cup OUT, and whether the
controller is in AUTO mode. A LabJack T7 with a relay driver can operate both ends.

Two things forced the decision rather than merely allowing it.

**Dose over an eight-hour irradiation cannot be measured by hand.** The quantity that
matters at the end of an irradiation is the integral of transmitted current over the
whole run. The cup is the only absolute current measurement on the beamline — the
four slit currents are a relative centring signal read through log amps and cannot be
integrated into a charge. Measuring the integral therefore requires the cup to enter
the beam on a schedule, for a few seconds, for hours, which is not something a person
does.

**Inference cannot bound a short insertion.** The thresholds in ADR 0002 were sized
for a hand insertion lasting minutes: a one-second debounce before a run opens, three
seconds below the release threshold before it closes. A one-second sampling insertion
never satisfies the debounce, so no run opens at all; a three-second one opens a
second late and closes three seconds after the cup has gone. The current-based
detector is not merely less precise than position for this duty cycle — it does not
work at all.

A third option existed and was rejected: keep inference and lengthen the dwell until
the debounce fits. That trades specimen exposure for nothing. The point of a sampling
insertion is to interrupt the irradiation as briefly as possible, and lengthening it
to accommodate a detector the hardware has made unnecessary is backwards.

## Decision

**1. Confirmed position replaces inferred current as the source of "is the cup in the
beam?"** The value comes from the controller's own status contacts, which report
where the cup is rather than where it was told to go.

**2. Commanded position and confirmed position are distinct, and only confirmed
position opens or closes a run.** There is a real mechanical lag from relay closure
through the controller and solenoid to the cup moving, and its magnitude is not yet
measured. Both timestamps are recorded on every insertion, which measures that lag
continuously as a side effect of normal operation.

**3. A commanded move that does not confirm within a timeout is a fault, not a
retry.** It disarms the sampling cycle and is raised visibly. The controller only
honours remote commands in AUTO mode, so a controller switched to LOCAL accepts a
closure and does nothing — the failure mode this decision exists to catch is an
unattended cycle that appears to run for hours while the cup never moves.

**4. Current inference is retained as fallback and as cross-check.** Position is
authoritative when the status contacts are readable and the controller is in AUTO.
Otherwise inference governs, so a hand insertion still records. Where both are
available and they disagree, the disagreement is written to the record as a fault and
is not resolved in favour of either.

**5. The detector seam is preserved exactly.** The position detector satisfies the
same one-value contract as the current detector, and
`CupAcquisitionStateMachine` does not change. A state machine that chose its
behaviour according to which detector it held would destroy the seam that made this
ADR cheap to write.

**6. The cup fails into the beam.** Cup-out requires two contact closures, so any
loss of drive — crash, device reset, disconnection, loss of coil power — returns the
cup to the beam path, where it intercepts the beam ahead of the specimen. This is a
property of the wiring and no software path is load-bearing for it.

**7. Dose is computed by zero-order hold, and every stage is recorded separately.**
Each insertion's current is held constant across the beam-on interval it represents.
Accumulated charge, fluence and dpa are written as separate columns, and the
displacement coefficient is operator-entered and recorded with its depth, its SRIM
version and its entry date.

## Consequences

The application now moves hardware in the beam path on its own schedule, which ADR
0002 explicitly did not do. That is a genuine increase in what a defect in this code
can cost, and it is why the fail-safe is wiring rather than software and why an
unconfirmed move stops the cycle instead of retrying.

Decision 6 has an uncomfortable edge worth stating: the safe state is also a state
that silently stops the irradiation. A power failure to the relay coils parks the cup
in the beam and the specimen quietly stops receiving dose. That is the right trade —
an interrupted irradiation is recoverable and an unrecorded one is not — but it means
"cup IN" is not by itself evidence that anything is working.

Decision 7 bakes an approximation into the archive. About one percent of an
irradiation is measured and ninety-nine percent is assumed constant between samples.
The error is whatever the beam drifted, it is not bounded by anything the application
can observe, and no downstream analysis can recover what was never sampled. The only
control is the cycle period, which is why it is an operator-editable field rather
than a constant. Every stage of the dose chain is recorded separately so that a later
reader can recompute any of it, and so that a better integration method can be
applied to the same archive if one is ever found.

Retaining inference as a cross-check (decision 4) means the application can report a
disagreement it cannot resolve. That is deliberate. The threshold values in
`cup_config.py` have never been validated against a real insertion, and the
disagreement rows produced over one irradiation are the first evidence anyone will
have about whether they are right.

Decision 5 costs something now to save something later: the detectors' input shapes
differ, and keeping one contract means widening it rather than branching inside the
state machine. The alternative is two run-logic paths that drift apart.

## Amendment, 2026-10-05: only automatic insertions count toward the dose

Decision 7 fed every closed run into the dose. In practice the cup is also inserted by
hand before an irradiation begins, with alumina still in front of the specimen, to check
the beam. Those insertions are measurements, not irradiation, and crediting them to the
dose overstates it.

C1. **Only automatic cup insertions contribute to the accumulated dose.** An insertion is
    automatic when automatic cup insertion commanded it. Every other insertion (the Insert
    button, a force start, a hand insertion at the controller in LOCAL) is recorded in the
    open cup log with its origin and is marked as not counted.

C2. **Time the cup spent in the beam during a non-counted insertion is excluded from the
    beam-on interval it falls inside.** The specimen was shadowed for that time, so the
    zero-order hold must not credit it.

C3. **An operator stop of automatic cup insertion asks for confirmation** and says what it
    does to the dose record. A stop forced by a fault (decision 3) does not ask.

C4. **Restarting after a stop, in the same cup log, asks whether the beam was on the
    specimen during the gap.** If yes, the gap is held at the last measured current like
    any other beam-on interval. If no, the gap is excluded. The answer, the gap's start
    and end, and whether the previous schedule was resumed are written to the cup log. The
    totals continue in the same file and nothing is overwritten.

C5. **Every new session asks whether to continue the dose from the previous session**, with
    no time limit, because an irradiation can span a weekend and more than one session.
    If continued, the gap between the two cup logs is put to the same question as C4, and
    the new cup log's header records the file the totals came from and their values. If
    not continued, the session starts at zero.

### Consequences of the amendment

The accumulated dose is now only as complete as the automatic insertions behind it. An
irradiation run entirely by hand accumulates nothing, by design.

C4 and C5 make the operator's word part of the dose. The application cannot see whether
the beam was on while it was not sampling, so it asks and records the answer next to the
numbers it affects. A reader can then recompute the dose under either answer.

## Amendment, 2026-11-03: an insertion may end when its mean is precise enough

Decision 7 and the sampling cycle gave every automatic insertion the same fixed dwell. A
fixed dwell is sized for the worst beam the operator expects, so on a quiet beam most of
it is spent measuring a current already known well enough, and every one of those
seconds shadows the specimen. The point of a sampling insertion is brevity; a dwell that
cannot end early works against it.

D1. **Two dwell modes.** In **fixed dwell** an insertion ends when its dwell elapses, as
    before. In **target uncertainty** it ends as soon as its **insertion uncertainty**
    (the relative standard error of its post-settle mean, see `CONTEXT.md`) is at or
    below the target, provided it holds at least the minimum number of post-settle
    samples; or when its **maximum dwell** is reached, whichever comes first. Fixed dwell
    remains the default. The fixed dwell and the maximum dwell are separate values and
    editing one never changes the other.

D2. **Automatic insertions always have a maximum dwell.** An automatic insertion on a beam
    that never converges would otherwise hold the cup in front of the specimen without
    limit, unattended, and could run past the next period boundary. "No limit" is
    available only for manual insertions, where the operator is at the bench.

D3. **A not-converged insertion still counts.** An insertion that reaches its maximum dwell
    before its target is marked not converged, and its mean still enters the dose if it is
    a counted insertion. It is the best estimate of the current available; dropping it
    would leave a beam-on interval with no current at all.

D4. **A minimum sample count guards the stopping rule.** Stopping the first time a running
    uncertainty dips under a target favours stopping on a lucky quiet stretch, and with
    one sample the spread is zero. No insertion may stop on precision before the minimum
    count (default 10, settings file only). The bias that remains is small and is recorded,
    not corrected.

D5. **Manual insertions may use the mode.** The live uncertainty is shown for every
    insertion. An operator setting, off by default, lets the Insert button and force start
    retract on convergence. A hand insertion at the controller is display-only. Manual
    insertions remain uncounted (C1) whatever ends them.

D6. **Every insertion records why it ended and when it first converged.** Mode, target,
    minimum count, maximum dwell, achieved uncertainty, sample count, end reason, and the
    time from settle-window end to first reaching the target are written per insertion.
    When the uncertainty cannot be computed the record says why (too few samples, no valid
    samples, mean negative, mean below run end current) rather than leaving it blank.
    Insertion uncertainty is computed and recorded in fixed-dwell mode too.

### Consequences of the amendment

Insertion length now depends on the data, and that is baked into the archive: two
insertions in one irradiation may differ in length by a factor of several, and the beam-on
interval each represents changes with them. D6 is what lets a later reader see why.

Insertion uncertainty assumes neighbouring samples are independent. If the beam wanders on
a timescale longer than the sample spacing, the computed uncertainty is too small and an
insertion can end on a precision it does not have. Faster polling makes this worse, not
better. Until a bench measurement of sample-to-sample correlation exists, the target is an
operator's judgement, not a guarantee.

None of this touches the dominant error in the accumulated charge, which is drift between
insertions (decision 7). A converged insertion is a precise sample, not a precise dose.
