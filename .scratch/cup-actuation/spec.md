# Spec: Faraday cup actuation — commanded insertion, sampling cycle, and dose tracking

Status: ready-for-agent
Date: 2026-09-15
Related: `docs/adr/0003-commanded-and-confirmed-cup-position.md`,
`docs/adr/0002-cup-acquisition-triggered-by-current.md`,
`docs/adr/0001-tests-first-and-no-muted-failures.md`,
`CONTEXT.md` (Beam interception and collection; Cup actuation and dose),
`.scratch/faraday-cup-reader/spec.md` (the reader this builds on)

> Read ADR 0003 before starting. It supersedes ADR 0002 decision 6 and records why
> position is authoritative over current inference, and why the cup fails to IN.
>
> ADR 0001 is binding on all test work here: a failing test is fixed or escalated,
> never muted.

## Problem Statement

The Faraday cup reader works. `Beamline` owns a Keithley 6482, publishes a cup
current snapshot, opens an acquisition run when the current crosses a threshold, and
writes every sample to a session file. All ten tickets of
`.scratch/faraday-cup-reader/` are done bar the bench verification in ticket 10.

What it cannot do is measure an irradiation. The cup is moved by hand, so a reading
happens when somebody walks over and inserts it. An irradiation on this beamline runs
eight hours or more, and the number that matters at the end of it is the dose the
specimen received — an integral over the whole run, not a handful of readings taken
whenever a person was free. Today that integral is assembled afterwards from numbers
written on paper, and the application contributes nothing to it.

Nothing else in the application can stand in. The four slit currents are a relative
signal: they say whether the beam is centred on the two axes, and they are read
through log amps whose absolute calibration nobody relies on. They cannot be
integrated into a charge. The cup is the only absolute current measurement on this
beamline, and it is only absolute while the cup is in the beam.

So the measurement has to be periodic and it has to be automatic: insert the cup for
a few seconds, read the transmitted current, retract, repeat every few minutes for
the length of the irradiation, and accumulate. The Faraday Cup Controller already
supports exactly this — its ESM remote connector takes dry-contact closures to
command the cup out and returns isolated contact closures reporting position — and
the LabJack T7 already in the rack can drive and read those contacts. None of that
path exists in the code: `labjack_driver.py` reads analog inputs and nothing else,
and the repository contains no digital output of any kind.

There is also a defect waiting in the existing reader the moment insertions get
short. `cup_config.py` sets `CUP_ARM_DEBOUNCE_S = 1.0` and
`CUP_RELEASE_INTERVAL_S = 3.0`, sized for a hand insertion lasting minutes. Trace a
one-second insertion through `CupDetector.update`: the current rises, `_arm_start_t`
is set, and the cup withdraws before `t - _arm_start_t >= 1.0`, so `_in_beam` never
becomes true and **no run opens at all**. At a three-second dwell a run does open,
one second late, and then stays open for three seconds after withdrawal, filling its
tail with baseline. Inference cannot be the run boundary for a sampling insertion.

## Solution

Drive the Faraday Cup Controller from the T7 through a relay pair, read the
controller's position status back through the same T7, and make confirmed position —
not inferred current — the boundary of an acquisition run.

Two digital outputs reach the controller's ESM connector as dry contact closures: one
enables cup-out control, one commands cup out. Three of the controller's isolated
status contacts come back as digital inputs: cup IN, cup OUT, and whether the
controller is in AUTO mode and therefore listening at all. The status lines join the
LabJack stream scan list, so position arrives in every window on the stream's own
sample clock alongside the slit currents, rather than costing a command-response
round trip.

A new position detector presents the same one-value contract the existing
`CupDetector` presents — "is the cup in the beam?" — but answers it from the
confirmed status contacts instead of from current. `CupAcquisitionStateMachine` is
unchanged. That substitution is the whole of ADR 0002 decision 6 being cashed in, and
it is why the seam was built.

On top of that sits a cycle scheduler: every N seconds, insert the cup, hold it for a
dwell, retract. Each insertion produces one acquisition run, one summary row, and one
absolute current. Between insertions the application holds that current constant and
accumulates charge against elapsed beam-on time — a zero-order hold, which is the
honest description of what a periodic sample can support. Charge becomes fluence
becomes dpa, with every stage written out separately so the arithmetic can be checked
by hand.

Manual insert and retract remain available at all times and override the cycle
completely. The cup fails to IN, where it acts as a beam stop.

## User Stories

1. As a beamline operator, I want to insert and retract the cup from the application,
   so that I do not have to walk to the cup to take a reading.
2. As a beamline operator, I want manual insert and retract available at any time,
   including while the cycle is running, so that the application never stands between
   me and the cup.
3. As a beamline operator, I want the cup's actual position shown from the
   controller's own status contacts, so that what I see is where the cup is and not
   what the application asked for.
4. As a beamline operator, I want commanded position and confirmed position shown as
   two separate things, so that a cup that did not move is visible as a cup that did
   not move.
5. As a beamline operator, I want a commanded move that never confirms to raise a
   loud, unmistakable fault, so that I find out immediately rather than at the end of
   the shift.
6. As a beamline operator, I want the application to tell me when the controller is
   not in AUTO mode, so that I understand why my commands are doing nothing.
7. As a beamline operator, I want to start an automatic sampling cycle, so that dose
   is tracked across an eight-hour irradiation without me attending it.
8. As a beamline operator, I want to set the interval between insertions and the
   dwell time of each one, so that I can trade measurement frequency against beam
   interruption without a code change.
9. As a beamline operator, I want to stop the cycle at any time, so that I can take
   the beamline back.
10. As a beamline operator, I want the cup to spend as little time in the beam as
    possible, so that the specimen's exposure is interrupted as little as possible.
11. As a beamline operator, I want the running dose total on screen, so that I can
    see progress toward the target without opening a file.
12. As a beamline operator, I want the cycle to refuse to arm until the displacement
    coefficient is entered, so that a long unattended run cannot produce charge with
    no dpa attached to it.
13. As a beamline operator, I want the cup to end up in the beam if the application
    or the computer dies, so that the specimen stops receiving dose rather than
    receiving an unrecorded amount of it.
14. As an experimenter, I want one summary row per insertion, so that I can see each
    measurement without reading every sample.
15. As an experimenter, I want each insertion's current computed only from samples
    taken after the instrument settled, so that range-hunting does not enter the
    average.
16. As an experimenter, I want the standard deviation of each insertion's samples
    recorded, so that I can see when a reading had not settled.
17. As an experimenter, I want the beam-on seconds since the previous insertion
    recorded, so that the interval each current is held across is explicit.
18. As an experimenter, I want accumulated charge, fluence and dpa written as
    separate columns, so that I can recompute each stage by hand and compare.
19. As an experimenter, I want the displacement coefficient recorded with its depth,
    its SRIM version and the date it was entered, so that a dpa figure can be traced
    to the number that produced it.
20. As an experimenter, I want the species, energy, charge state and irradiated area
    used for the calculation written into the session file, so that nothing in the
    dose chain is implicit.
21. As an experimenter, I want commanded and confirmed timestamps recorded for every
    insertion, so that I can measure the mechanical lag rather than assume it.
22. As an experimenter, I want cup IN and OUT transitions in the record, so that the
    specimen's interrupted exposure is reconstructible.
23. As a developer, I want the digital outputs owned by `Beamline` like every other
    instrument, so that the one-instrument-one-owner invariant holds.
24. As a developer, I want position status carried on the existing stream, so that
    reading it costs no command-response traffic and shares the stream's timestamps.
25. As a developer, I want the position detector to satisfy the same contract as the
    current detector, so that `CupAcquisitionStateMachine` does not change.
26. As a developer, I want the cycle scheduler free of Qt and of the clock, so that
    an eight-hour cycle can be tested in milliseconds.
27. As a developer, I want the dose arithmetic to be pure functions over floats, so
    that it is testable against numbers worked by hand.

## Implementation Decisions

### The hardware chain

The CB37 terminal board is the hub; the T7's own screw terminals are not used. The
LJTick-RelayDriver is wired by flying leads rather than plugged into a terminal
block — its datasheet states that the VS pin "is not used by the LJTRD and thus is
not connected to anything," so only GND, IOA and IOB carry signal.

| CB37 pin | Signal | Role |
|---|---|---|
| 6 | FIO0 | LJTRD IOA → relay 1 → dry contact across ESM P1 **5–13**, enable cup OUT control |
| 24 | FIO1 | LJTRD IOB → relay 2 → dry contact across ESM P1 **1–9**, command cup OUT |
| 5 | FIO2 | cup **IN** status contact, other side to GND |
| 23 | FIO3 | cup **OUT** status contact, other side to GND |
| 4 | FIO4 | controller **AUTO mode** status contact, other side to GND |
| 1 | GND | LJTRD GND and the common side of all three status contacts |
| 27 | Vs | relay board VCC, input side only |

The relay board's JD-VCC jumper is removed and the coil supply comes from a separate
5 V source, so the coil and contact side is not sharing the LabJack's supply on a
beamline that arcs at rough vacuum.

The relay board is not optional and not redundant with the LJTRD. The controller
requires a *dry* contact closure between its ESM pins; the LJTRD is a low-side switch
referenced to LabJack ground through a 22 Ω resistor and cannot provide one. The
LJTRD's job is to sink the relay board's active-low input, which the T7's digital
outputs cannot drive directly.

Polarity, stated once because it is where this kind of work goes wrong: LJTRD
output-high closes its switch, which pulls the relay board's active-low input down,
which energises the relay. A status contact that is **closed** pulls its FIO line to
ground and therefore reads as bit value **0**. Closed is zero. The decoding function
inverts.

### Fail-safe: the cup fails IN

Cup OUT requires *both* closures. Any failure — application crash, T7 reset, USB
disconnect, loss of power to the relay coils — opens both and the cup returns IN,
where it intercepts the beam before the specimen. This is the desired direction: the
cup is built to absorb beam, and the specimen is the thing being protected from
receiving dose nobody is recording.

No software path is load-bearing for this. It is a property of the wiring, and it
must stay a property of the wiring. Do not add a shutdown handler that drives the cup
anywhere; the absence of drive *is* the safe state.

Relay 2 (command OUT) is the one that cycles. Relay 1 (enable) is held closed while
the application is connected and in control, and opened on disconnect and on
application shutdown, as an explicit release of control.

### The T7 digital path

`labjack_driver.py` gains digital I/O. It has none today.

**Status is streamed, not polled.** `FIO_STATE` is a streamable register on the T7.
It joins the scan list of **both** stream profiles in `labjack_stream_config.py`, so
the three status bits arrive in every window at the window rate, timestamped by the
stream's own sample clock, in lockstep with the slit currents. The T7 does not permit
mid-stream scan-list changes — `labjack_stream_worker.py`'s docstring is explicit —
so this is a permanent addition to both profiles, not a runtime toggle.

The window payload's `channels` dictionary currently holds only AIN entries. It gains
one non-AIN entry for `FIO_STATE`. Every existing consumer of `window_ready` and
`raw_window_ready` must tolerate it; a consumer that iterates the dictionary assuming
AIN-shaped values will break, and finding those is part of this work.

**Commands are command-response, through the stream worker.** Writing a digital
output while a stream runs is permitted on the T7 — the documented restriction is
that *analog inputs* cannot be read by command-response during a stream, which this
feature never needs. But two threads must not use one LJM handle concurrently, and
`labjack_stream_worker.py` owns the handle for its lifetime. So the worker gains a
thread-safe pending-write slot that it drains between `eStreamRead` calls, and
publishes the resulting output state as a signal. No other thread touches the handle.
Writes happen a handful of times per cycle period, so the cost to the read loop is
negligible.

**Layering.** The digital output surface belongs to `Beamline` through a new
`CupActuationLinkMixin` in `rbl/state/`, shaped like the existing `vacuum_link.py`.
No widget constructs, holds, or tears down anything. The mixin publishes a frozen
snapshot carrying `connected` like every other snapshot.

### Position: commanded, confirmed, and the detector contract

The cup position snapshot carries, at minimum: commanded position, confirmed position
derived from the status bits, whether the controller is in AUTO, whether the status
reading is stale, and the timestamp of the last confirmed transition.

Confirmed position is derived by a **pure function** taking the raw `FIO_STATE`
integer and returning a typed position value. It handles the impossible states
explicitly: both IN and OUT asserted, and neither asserted. Neither-asserted is the
normal reading while the cup is in transit. Both-asserted is a wiring or controller
fault and must be reported as unknown, never silently resolved in favour of one.

A new `CupPositionDetector` in `rbl/services/cup_acquisition.py` exposes
`cup_in_beam` exactly as `CupDetector` does. `CupAcquisitionStateMachine` takes a
detector and must not change. Where the two detectors' `update` signatures differ,
widen to a reading structure rather than special-casing inside the state machine —
the state machine choosing its behaviour based on which detector it holds would
destroy the seam ADR 0002 built and ADR 0003 relies on.

Position is authoritative whenever the status contacts are readable and the
controller is in AUTO. Current inference is the fallback otherwise, so a hand
insertion with the controller in LOCAL still records. When both are available and
they disagree — position confirms IN but current stays below the arm threshold, or
the reverse — that disagreement is recorded as a fault row. It is not resolved, and
neither source is silently preferred. Over one irradiation those rows are the first
real evidence anyone will have about whether the threshold values in `cup_config.py`
are right.

### Run boundaries and the two constant sets

A position-confirmed run opens the moment the IN contact confirms and closes the
moment the OUT contact confirms, subject only to a short contact debounce for relay
and switch bounce.

`CUP_ARM_DEBOUNCE_S` and `CUP_RELEASE_INTERVAL_S` continue to own the
current-inference path and are **never** applied to a commanded insertion. Add a
separate, separately named set of position constants. Do not reuse the existing four
by lowering them: hand insertions still happen, they still need hysteresis, and one
set of numbers serving two mechanisms is how the next person breaks both.

New configuration, all in `rbl/config/cup_config.py`:

- contact debounce, 0.05 s
- move confirmation timeout, 2.0 s
- cycle period, 300.0 s default
- cycle dwell, 3.0 s default
- per-insertion settle window, 1.0 s default, excluded from the mean
- the three status bit positions and the two output line names

### The sampling cycle

A pure scheduler object in `rbl/services/`. It receives the current time as an
argument and returns the action to take; it does not import Qt and it does not call
the clock. This is a hard requirement, not a preference: an eight-hour cycle whose
behaviour depends on a real clock cannot be tested, and a cycle that has never been
tested across a fault is a cycle that will fail silently across a fault.

Behaviour:

- Disarmed by default. Arming is an explicit operator action.
- **Arming is refused unless the displacement coefficient, species charge state and
  irradiated area are all present.** The purpose of the cycle is reaching a target
  dpa; a cycle that runs for eight hours and produces charge with no dpa attached has
  failed at its only job.
- Period and dwell are operator-editable from the tab, using
  `gui/widgets/inputs.py`, never a bare spin box.
- A manual insert or retract takes precedence immediately.
- A scheduled insertion that falls due while a manual run is open is skipped and
  recorded as skipped, not queued.
- Any move that fails to confirm within the timeout disarms the cycle and raises a
  fault.

### The dose chain

Four stages, each written out separately:

1. **Insertion current** — the mean of the samples in one run taken after the settle
   window, in amperes, with its sample count and standard deviation.
2. **Accumulated charge** *Q* — zero-order hold. Each insertion's current is held
   constant across the beam-on interval it represents and multiplied by that
   interval's length, and the products accumulate.
3. **Fluence** Φ = *Q* / (*q* · *e* · *A*), where *q* is the charge state from the
   species table, *e* is the elementary charge, and *A* is the irradiated area from
   the raster planner's sample region.
4. **dpa** = Φ · *k*, where *k* is the displacement coefficient.

*k* is operator-entered and cannot be derived by this application. It comes from
SRIM, it is depth-dependent, and nothing in the repository can compute it. It is
entered with its depth, the SRIM version, and the date of entry, and all four are
written into the session file header. A dpa figure whose *k* cannot be traced is not
a result.

The arithmetic lives in `rbl/hardware/` as pure functions over plain floats, in the
manner of the existing physics modules, so it can be tested against numbers worked by
hand. It does not live in the writer and it does not live in the tab.

The zero-order hold is an approximation and the spec says so plainly: about one
percent of an eight-hour irradiation is measured and the rest is assumed constant
between samples. The error is whatever the beam drifted, and it is not bounded by
anything the application can observe. The only control over it is the cycle period,
which is why the period is an operator-editable field rather than a constant. The
slit currents cannot improve this — they are a relative centring signal, not an
absolute current, and cannot be integrated into a charge.

### The record

`cup_session_writer.py` today writes sample rows, lifecycle markers, per-run sample
counts and session metadata. It computes no charge, no dose, and no beam-on time.
That is new work here, not a modification of something that already runs.

Additions:

- A **per-insertion summary row**: run id, commanded timestamp, confirmed timestamp,
  dwell, sample count, post-settle mean current, standard deviation, beam-on seconds
  since the previous insertion, and the running *Q*, Φ and dpa after this insertion.
- **Position transition markers** for every confirmed IN and OUT, so the specimen's
  interrupted exposure is reconstructible.
- **Fault rows**: move-not-confirmed, controller-not-in-AUTO, impossible status
  combination, position-versus-current disagreement, cycle disarmed.
- A **session header** carrying species, energy, charge state, irradiated area, *k*
  with its depth, SRIM version and entry date, and the cycle period and dwell in
  force.

Flush after every row, as the existing writer does and for the same reason.

### Presentation

All of it on the existing Faraday Cup tab. Commanded and confirmed position are shown
as two distinct indicators — a cup that was told to move and did not is the specific
failure this feature must make visible, and one combined indicator hides exactly
that. Cycle state, time to next insertion, running dpa against target. Faults use
theme roles from `config/theme.py`, never hex codes. The tab owns no driver and
converts nothing.

## Testing Decisions

ADR 0001 is binding. A failing test is fixed or escalated, never muted.

Tests assert on external behaviour: what a screen renders, what a file contains, what
a transition does. Not on private widget attributes, and not on whether a particular
internal method was called.

### The integration seam

Extend the existing cup feed helper in the test payload module so that a test can
inject **raw `FIO_STATE` integers** alongside the raw SCPI response strings it
already injects, into a real `Beamline` wired as the main window wires it. Everything
above LJM and PyVISA is then inside the test: status decoding, the position detector,
the state machine, the scheduler, the dose arithmetic, the tab render and the file.
Follow the existing LabJack feed's shape and read its docstring first.

### Direct unit tests, no Qt and no hardware

- **Status decoding**, fed raw integers: cup IN, cup OUT, in transit with neither
  asserted, both asserted, AUTO asserted and not, and the bit-inversion case that
  proves closed reads as zero.
- **The position detector**, proving it satisfies the same contract as `CupDetector`
  and that `CupAcquisitionStateMachine` behaves identically given either.
- **The cycle scheduler**, fed an explicit timestamp series: a full eight-hour cycle
  at the default period; a move that never confirms, disarming the cycle; a manual
  insert during an armed cycle; a scheduled insertion falling due during a manual
  run, which must be skipped and not queued; arming refused with *k* absent.
- **The dose arithmetic**, against values computed by hand: a single insertion held
  across a known interval; several insertions at different currents; a zero-current
  insertion; and the full charge-to-fluence-to-dpa chain for one worked example whose
  expected numbers are written into the test.

Because timestamps are inputs throughout, **no test in this effort sleeps**, and none
needs to.

### Regression on what already ships

The one-second-insertion case must be tested explicitly against the existing
current-inference path, asserting that it opens no run — documenting the limitation
that motivates position-triggered runs, rather than leaving it as a surprise. And
adding `FIO_STATE` to both stream profiles changes the window payload: a test must
assert that existing consumers of `window_ready` and `raw_window_ready` still work.

## Out of Scope

- **Commanded cup insertion for any cup but #3.** The ESM connector addresses one
  cup. Nothing here generalises to a second.
- **Any use of the picoammeter's voltage bias sources.** Unchanged from the reader
  spec: the outputs stay asserted off.
- **The BIC/AUX analog path.** The controller's J7 input accepts ±10 V to display
  current or dose from an external integrator. It is a real alternative route and it
  is not this one.
- **Improving the zero-order hold.** No interpolation, no drift model, no use of slit
  current to fill the gaps between insertions.
- **dpa depth profiles.** One coefficient at one stated depth. A depth-resolved
  profile is analysis work and belongs in rbl-analysis.
- **Reading SRIM output files.** *k* is typed in.
- **Closing the loop on dose.** The application does not stop the beam, move the
  slits, or alter the raster when a target dpa is reached. It reports.
- **Any interlock on insertion.** Deliberate. There is no vacuum, HV, or raster
  precondition on moving the cup.
- **Relay lifetime management.** Roughly 288 operations a day on the cycling channel
  approaches a commodity relay's mechanical life within about a year of continuous
  use. Irradiations are not daily and the relay is replaceable, so this is recorded
  and not engineered around.
- **Changes to the slit current path, the log amps, or beam reconstruction.**

## Further Notes

**Two numbers in this spec are guesses and must be measured.** Each is its own bench
ticket, and neither blocks the software.

The first is the 6482's autorange settle time on a step from baseline into the beam.
The reader spec commits to autoranging always enabled; at a three-second dwell and
10 Hz that is thirty readings, and nobody knows how many are spent range-hunting. The
measured answer sets the settle window and the minimum useful dwell, and if it turns
out that autoranging cannot settle inside a usable dwell, the fix is a fixed range
during commanded insertions — which would amend the reader spec's autorange decision
and needs recording in the ADR rather than being done quietly.

The second is the mechanical lag from relay closure through the controller and the
solenoid to the cup actually moving. That lag is precisely why confirmed position
rather than commanded position is the run boundary, and recording both timestamps on
every insertion measures it continuously for free.

**The bench bring-up is destructive if it is wrong.** The first end-to-end test
drives a real cup into a real beamline. Verify the wiring against a resistor, as the
LJTick-RelayDriver datasheet describes, and verify the status decoding with the cup
moved by hand, before any software commands a move.

**ADR 0002 is not retired.** Its current-inference detector remains in service for
hand insertions and as the cross-check. ADR 0003 supersedes decision 6 only.
