# CONTEXT

The vocabulary of the Right Beamline (RBL) control application: what each word
means in this project. Terms only — no implementation detail, no design notes,
no plans. Those live in `docs/adr/` and `docs/`.

If a term here and the code disagree, one of them is wrong. Say which, and ask.
Do not silently pick a side.

---

## Beam optics

**Steerer** — the pair of electrostatic deflection plates that bend the beam on
one axis.

**Differential voltage** — the plate-to-plate voltage across a steerer axis.
**Per-plate voltage** is exactly half of it. Voltages are quoted differentially
unless a figure is explicitly labelled per-plate. The two happen to coincide
numerically at a generator gain of 1000, which is why per-plate figures are
always labelled rather than left to context.

**Deflection** — the *angle* the steerer imparts to the beam. Plate length
affects the angle and only the angle.

**Displacement** — the *distance* that angle produces at the point of interest.
It is the deflection angle times the drift distance, and nothing else: no pivot
correction, no geometric fudge.

**Drift distance** — the free flight path between the steerer and the point of
interest. See *Known collisions* below.

**Turnaround** — the region at each end of a raster sweep where the beam
reverses direction and therefore lingers. Sizing a sweep so that the turnaround
falls off the sample (or off the slit jaws) is a deliberate goal, not an
accident of the geometry.

**Slit reconstruction** — inferring where the beam is from the currents landing
on four slit jaws. A centred beam is solvable without knowing the beam width; an
off-centre one is not, and the reported interval widens to say so rather than
returning a confident number.

---

## Instruments and readings

**Window** — one block of samples off the stream, covering a short span of time,
carrying every channel that the active profile scans. The unit in which readings
arrive.

**Profile** — a named scan list. It determines which channels appear in a window
and at what rate. A channel absent from the active profile reads as absent, not
as zero.

**Snapshot** — a typed, converted reading derived from a window. Raw volts become
amps, kilovolts and milliamps exactly once, before any screen sees them. Screens
render snapshots; they do not convert.

**Device view** — one device's state as rendered on one tab. A single device
usually has several views, on different tabs.

**Cross-tab agreement** — the invariant that every view of one device shows the
same value for that device. An operator reading a number off one screen and
acting on it must not be able to get a different number from another screen.

---

## Calibration

**Ladder** — the ordered set of setpoints a calibration sweep visits.
A **rung** is one setpoint on it.

**Pass type** — the order in which one pass traverses the ladder. `up` and
`down` each visit every rung twice, out and back on each polarity; `random`
visits each rung once, shuffled. The three pass types therefore produce
sequences of *different lengths*, by design. Any code or test that assumes they
are the same length is wrong.

**Hard trip** — an immediate, unconditional abort on an over-current reading.
Checked in every state, using the unclamped conversion so that a railed monitor
reads as a huge number rather than as no number at all. It exists to catch a
dead short.

**Soft trip** — an abort raised only once the current has stayed above the
continuous limit for a minimum duration across consecutive windows. It exists to
catch a sustained overload without firing on a transient.

---

## Amplifier limits and load

**Continuous rating** - the 20 mA an amplifier may supply for as long as it is
driven. The EEL5000 manual calls it "20 mA peak DC".

**Burst rating** - the 100 mA an amplifier may supply for at most 4 ms at a time.
A burst is never an operating point; it is the headroom a spike may use.

**Recovery period** - the 100 ms after a burst during which the manual says the
amplifier supplies at most 10 mA before another burst is available.

**Operating point** - the shape, frequency and amplitude an amplifier is commanded to
hold. Changing any of the three is a new operating point.

**Reference current** - the steady current an amplifier draws during the first minute
at an operating point, together with the noise measured over that same minute. It is
captured again whenever the operating point changes.

**Spike threshold** - twice the reference current, raised only as far as needed to sit
five noise widths above the reference. With a quiet monitor it is simply twice the
reference.

**Current spike** - one interval during which an amplifier's current exceeds the spike
threshold. Its duration, peak and charge describe it. A spike shorter than the monitor
and sampling can resolve has a known charge but only a lower bound on its peak. The
continuous and burst ratings are fixed reference levels, not what defines a spike.

**Load capacitance** - the capacitance an amplifier output drives, measured, never
assumed. It belongs to a load condition and is quoted with when and how it was
measured.

**Load condition** - what is physically connected to an amplifier's output during
a measurement: *disconnected* (the amplifier alone), *cable only* (the HV cable
attached, its far end open) or *on plates* (cable, feedthrough and steerer
plates). Differences between conditions locate where the capacitance lives.

**Characterization result** - one measured load capacitance (and leakage) for one
amplifier at one plate position, under one load condition, by one method, at one time.
Results are kept, never overwritten; the newest one for each combination is what the
application plans from, shown with its age.

**Hardware change** - a dated note that the load may have changed (an amplifier swap is
one kind). Characterization results older than the latest hardware change are shown as
predating it.

**Plate position** - one of the four steerer plates an amplifier can drive: X+, X-,
Y+ or Y-. A position is a place on the beamline, not an amplifier.

**Amplifier** - one physical EEL5000 unit, known by its serial number. An amplifier
can move between plate positions; its measurements move with it.

**Amplifier assignment** - which amplifier drives which plate position, from a given
date. Every load measurement belongs to the assignment in force when it was taken.

**Amplifier swap** - the recorded change from one amplifier assignment to the next,
with its date and an optional note.

---

## Beam interception and collection

**Slit current** — the current a single slit jaw intercepts. There are four, one
per jaw.

**Cup current** — the current collected by the Faraday cup, which sits
downstream of the slits. It is the part of the beam that passed through the
aperture rather than landing on a jaw. One number, measured in a different place
by a different instrument. It is not a fifth slit current.

**Transmitted current** — a synonym for cup current, preferred where the point
being made is the physics (what got through) rather than which instrument read
it.

**Insertion** — one period during which the Faraday cup is in the beam path. The
cup is moved by hand and reports no position, so the application infers an
insertion from the current it sees rather than being told about it.

**Acquisition run** — the samples recorded across one insertion. One insertion
produces one run.

**Idle ping** — a reading taken at low rate while no run is active. Its only job
is to notice an insertion beginning. It is evidence that the application was
watching; it is not measurement data.

**Run start current** and **run end current** — the cup currents at which a run
starts and ends. The run end current is deliberately the lower of the two, so
a current sitting near the boundary cannot start and stop runs repeatedly. ADRs 0002
and 0003 and the code call these the **arm threshold** and **release threshold**; on
screen and in prose they are run start current and run end current.

---

## Cup actuation and dose

**Commanded position** — where the application has told the cup to go. It is a
statement about what was asked for, never about where the cup is.

**Confirmed position** — where the Faraday Cup Controller's own status contacts say
the cup is. This is the authority. Between the two lies a real mechanical lag, and a
cup that was commanded and did not move is the specific failure the pair exists to
expose.

**In transit** — neither the IN nor the OUT status contact asserted. The normal
reading while the cup is moving. Distinct from **indeterminate**, where both are
asserted at once, which is a wiring or controller fault and is never resolved in
favour of one.

**AUTO mode** — the controller state in which it honours remote commands. In LOCAL it
accepts a contact closure and does nothing, so the application reads the AUTO status
contact and does not assume it.

**Sampling insertion** — a short, commanded insertion whose purpose is to measure the
transmitted current once, not to irradiate anything. Seconds, not minutes. The thing
a sampling insertion is optimised for is brevity: every second it spends in the beam
is a second the specimen is not being irradiated.

**Sampling cycle** — the repeating schedule of sampling insertions across an
irradiation. Its **period** is the interval between insertions and its **dwell** is
how long each one lasts.

**Settle window** — the span at the start of an insertion during which the
picoammeter is still autoranging and its readings are not yet trustworthy. Samples
inside it are recorded but excluded from the insertion's mean.

**Beam-on interval** — the span of time that one insertion's measured current is held
to represent. It runs between insertions, not during them: while the cup is in the
beam, the specimen is not being irradiated.

**Accumulated charge** — the integral of transmitted current over the irradiation, by
zero-order hold: each insertion's current multiplied by its beam-on interval, summed.
It is the honest description of what a periodic sample supports, and it is an
approximation whose error is whatever the beam drifted between insertions.

**Displacement coefficient** — displacements per ion per unit fluence, quoted at a
stated depth. It comes from SRIM, it is entered by an operator, and nothing in this
application can derive it. A dpa figure whose coefficient cannot be traced to a
version and a depth is not a result.

**Fails into the beam** — the property that any loss of drive returns the cup to the
beam path. It belongs to the wiring, not to any code path, and must stay that way.

**Acquisition settings** - the five values an operator can change on the Faraday Cup
tab that decide how a run is bounded and which of its samples count: arm threshold,
release threshold, settle window, cycle period, cycle dwell. Two further values, the
arm debounce and the release interval, are settings but not operator-editable: they
change in the settings file only, with the application closed. See ADR 0002,
amendment 2026-09-24.

**Settings file** - `~/.config/rbl/cup_settings.json`, the stored acquisition
settings. The application owns it while running and rewrites it after every committed
edit; a hand edit made while the application is open is lost. `cup_config.py` holds
the defaults, and the file holds only what an operator changed.

**Settings lock** - the state in which the arm threshold, release threshold and settle
window cannot be edited, because an acquisition run is open or automatic cup insertion
is running. Locked fields are disabled, never silently ignored. Period and dwell are never
locked.

**Pending change** - an edit to the cycle period or dwell that has been accepted but
not yet applied, because applying it would alter an insertion in progress or a
countdown already running. It is displayed with the time it takes effect: the next
period boundary for a period change, the next insertion for a dwell change.

**Saved boundary** - the time automatic cup insertion would have inserted next when it
was stopped. It is kept so that restarting can resume the original schedule rather than
starting a fresh countdown, and it is dropped once the application closes, once the cup
log closes, or once a fresh start sets a boundary of its own. A saved boundary already in the past cannot be resumed and
never produces a catch-up insertion.

---

## Logging and sessions

**Monitoring log** — the continuous vacuum log. It starts when the application opens,
rolls to a new file at local midnight, and files by month under `data/vacuum/YYYY-MM/`.
It is the only log that runs without being asked for and the only one that rolls over.
See ADR 0004.

**Session** — one Start Session to Stop Session on the Overview tab, and one folder.
Everything recorded for an experiment goes inside it: the beamline CSV, the event log,
the session's `vacuum.csv`, the session's cup log and any video. Video is optional and
may start and stop any number of times within a session. A session never rolls over.

**Cup log** — the Faraday cup record for one session or one test. It is open only when
an operator opened it. A cup log opened with a session lives in the session folder; one
opened from the Overview cup panel without a session is a **test cup log** and lives
under `data/faraday_cup/YYYY-MM/`. At most one cup log is open at a time.

**Not logging** — the state with no cup log open. The cup still reads, moves by hand and
displays, and nothing is written. Shown in the WARN colour wherever cup data is shown.

**Automatic cup insertion** — the sampling cycle running: the application inserting and
withdrawing the cup on its own schedule. It runs only while a cup log is open. Formerly
called *arming the cycle*. Starting it is "start automatic cup insertion", never "arm".

**Manual insertion** — any insertion automatic cup insertion did not command: the Insert
button, a force start, or a hand insertion at the controller. Recorded when a cup log is
open, never counted toward the dose. Typically a beam check before irradiation begins.

**Counted insertion** — an automatic insertion, the only kind that contributes to the
accumulated dose.

**Excluded interval** — a span of time removed from the beam-on interval it falls in:
the cup-in time of a manual insertion, or a stop gap the operator said had no beam on the
specimen.

**Stop gap** — the time between an operator stopping automatic cup insertion and starting
it again in the same cup log. On restart the operator says whether the beam was on the
specimen during it; the answer decides whether it is held at the last current or
excluded.

**Dose continuation** — carrying the accumulated totals from the previous session's cup
log into a new session's. Offered at every session start, with no time limit, because an
irradiation can span more than one session.

---

## Known collisions

**"Beam current" means two things.** The four slit currents and the one cup
current are both current from the beam, read in different places, and they do
not agree with each other — nor should they. Prefer *slit current* and *cup
current*. Do not write *beam current* unqualified.

**"Trip" means two things.** The application's *hard trip* and *soft trip* are
aborts it raises from the current monitor. The amplifier's own LIMIT and TRIP are
front-panel modes of its internal current circuit: LIMIT clamps the current and the
output stops following its input; TRIP shuts the output off. Say *amplifier LIMIT*
or *amplifier TRIP* for the hardware, never bare "trip".

**"Drift" means two unrelated things.** In optics it is the flight distance
between the steerer and the sample. In calibration it is a long-running pass
that holds a setpoint and watches it wander over hours. Prefer *drift distance*
and *drift pass* wherever both could be meant.

**"Position" means commanded or confirmed, never both.** They differ by a mechanical
lag and, when something is wrong, by more than that. Say which one is meant.

**"Session" used to mean two things.** The cup writer (`CupSessionWriter`) and older
code called one run of the application a session. A session is now only Start Session
to Stop Session on the Overview tab. The cup's file is a *cup log*. Do not use
*session* for an application run.

**"Arm" used to mean two things.** The arm threshold (a current) and arming the cycle (an
action). On screen these are now *run start current* and *start automatic cup
insertion*. The code identifiers keep the old names.
