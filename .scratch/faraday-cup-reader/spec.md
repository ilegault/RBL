# Spec: Faraday cup reader — cup current over GPIB, with threshold-triggered acquisition

Status: ready-for-agent
Date: 2026-09-09
Related: `CONTEXT.md` (Beam interception and collection),
`docs/adr/0002-cup-acquisition-triggered-by-current.md`,
`docs/adr/0001-tests-first-and-no-muted-failures.md`

> Read ADR 0002 before starting. It records why the application infers an insertion
> from the cup current rather than being told about it, and decision 6 in it
> constrains how the run logic must be shaped.
>
> ADR 0001 is binding on all test work here: a failing test is fixed or escalated,
> never muted.

## Problem Statement

The application can see what the beam does to the slits and nothing about what gets
past them.

Four NEC log amps report slit current — the current each jaw intercepts — and that
is the only current reading the application has. It is a measure of what the beam
is hitting on its way through, not a measure of the beam that arrives. For an
irradiation, the number that matters is the transmitted current: the charge that
actually lands on the sample position, integrated over the exposure. Nothing in the
application reports it.

A Faraday cup and a Keithley 6482 picoammeter now exist on the beamline to measure
exactly that, connected over GPIB through a Keysight 82357B USB adapter. Today an
operator reads the number off the instrument's front panel and writes it down. That
number is never recorded alongside the slit currents, the vacuum pressure, the
deflection voltages, or anything else in the session log, so an irradiation cannot
be reconstructed afterwards from the application's own records.

The cup is inserted by hand at roughly five-minute intervals and reports no
position. An operator at the cup is not also at the keyboard, so a design that
requires a button press to begin recording either loses the first seconds of every
insertion or loses whole insertions on a busy shift. Dose is an integral; a
recording that starts late is not a smaller measurement, it is a wrong one.

Separately, the application's vocabulary cannot currently express any of this. The
tab that shows slit current is titled "Beam Current". A second screen showing cup
current would also, in plain language, be showing beam current — and the two
quantities are different, measured in different places by different instruments,
and do not agree. `CONTEXT.md`'s cross-tab agreement invariant exists to stop
exactly this: an operator must not be able to read a number off one screen, act on
it, and have been looking at a different quantity than they thought.

## Solution

Add cup current to the application as a first-class reading, owned by `Beamline`
like every other instrument, rendered on its own tab, and recorded automatically
for the duration of each insertion.

The picoammeter is polled over GPIB. While no acquisition run is active it is
polled slowly — an idle ping whose only job is to notice a cup going into the beam.
When cup current rises past the arm threshold and stays there, an acquisition run
begins and polling moves to the acquiring rate. When it falls below the release
threshold and stays there, the run ends. Every sample inside a run is written to a
session log; idle periods are represented by markers rather than by rows, so the
record still distinguishes "the cup was out and the application was watching" from
"the application was not running" from "the instrument was disconnected". An
operator can force a run to start or stop at any time, overriding the detector.

The vocabulary is fixed at the same time. The existing tab is retitled to Slit
Currents, the new one is Faraday Cup, and the terms are already written into
`CONTEXT.md`. The tab order is reworked so that the screens follow the order an
operator actually uses them in.

## User Stories

1. As a beamline operator, I want the cup current displayed live in the
   application, so that I do not have to read it off the instrument's front panel.
2. As a beamline operator, I want the cup current shown in familiar engineering
   units with automatic scaling, so that I can read nanoamps and milliamps off the
   same field without doing arithmetic.
3. As a beamline operator, I want acquisition to start on its own when I insert the
   cup, so that I do not have to be at the keyboard with both hands full at the cup.
4. As a beamline operator, I want acquisition to stop on its own when I withdraw the
   cup, so that the run does not fill with baseline noise after the measurement.
5. As a beamline operator, I want a brief sustained reading required before a run
   starts, so that a single noisy sample does not open a one-sample run.
6. As a beamline operator, I want the current to have to fall meaningfully below the
   starting level before a run ends, so that a beam sitting near the threshold does
   not produce a hundred fragmentary runs.
7. As a beamline operator, I want to force a run to start regardless of what the
   detector thinks, so that I can record a measurement the thresholds would miss.
8. As a beamline operator, I want to force a run to stop, so that I can end a run
   the detector has failed to close.
9. As a beamline operator, I want to see plainly whether a run is currently active,
   so that I know whether what I am doing is being recorded.
10. As a beamline operator, I want to see how long the current run has been going
    and how many samples it holds, so that I can tell a healthy run from a stalled one.
11. As a beamline operator, I want a live running average of the cup current across
    the active run, so that I have the number the measurement is actually for.
12. As a beamline operator, I want that average computed from the samples that were
    logged, so that the number on screen and the number in the file cannot disagree.
13. As a beamline operator, I want a live plot of cup current over time, so that I
    can see whether the beam is steady during an insertion.
14. As a beamline operator, I want the tab to work with nothing connected, so that
    the application is usable away from the beamline.
15. As a beamline operator, I want a disconnected tab to say it is disconnected
    rather than showing a stale or zeroed number, so that I never mistake no-data
    for a real reading.
16. As a beamline operator, I want to connect the picoammeter from the Faraday Cup
    tab alone, so that I can bring up just the cup without connecting everything.
17. As a beamline operator, I want the picoammeter included in Connect All, so that
    the normal startup sequence brings it up with everything else.
18. As a beamline operator, I want the picoammeter's connection state visible in the
    same way as every other instrument, so that I do not have to learn a new idiom.
19. As an experimenter, I want every sample of every run written to a session file,
    so that I can integrate dose myself rather than trusting a summary.
20. As an experimenter, I want one file per session rather than one per insertion,
    so that a shift produces one artefact instead of a pile of them.
21. As an experimenter, I want each row tagged with which run it belongs to, so that
    I can still separate insertions during analysis.
22. As an experimenter, I want each sample to carry both the host timestamp and the
    instrument's own timestamp, so that I can detect polling stalls.
23. As an experimenter, I want each sample to carry the instrument's status word, so
    that I can tell a trustworthy reading from one taken over-range.
24. As an experimenter, I want over-range samples flagged explicitly rather than
    silently discarded, so that I can see that they happened.
25. As an experimenter, I want the thresholds in force recorded with each run, so
    that I can tell later why the application decided a run had begun.
26. As an experimenter, I want periodic evidence in the file that the application was
    polling during idle periods, so that a gap between runs is not ambiguous.
27. As an experimenter, I want disconnection and reconnection recorded in the file,
    so that a gap caused by a lost instrument is distinguishable from a cup that was
    simply out of the beam.
28. As an experimenter, I want the file flushed after every row, so that a run that
    dies at hour eleven leaves eleven hours of usable data.
29. As an experimenter, I want the file to live alongside the application's other
    data output, so that I collect a session's records from one place.
30. As an operator reading two screens, I want slit current and cup current to be
    named differently everywhere, so that I cannot confuse one for the other.
31. As an operator reading two screens, I want the slit and cup screens adjacent, so
    that comparing what was intercepted with what got through is one glance.
32. As an operator, I want the tabs ordered the way I work — check the machine,
    measure the beam, drive the beam, then the rare setup screens — so that the
    common path is a short walk.
33. As an operator, I want the application to open on Overview, so that the first
    thing I see answers what the beamline is doing right now.
34. As a physicist, I want the cup's voltage bias sources to remain off unless
    deliberately commanded, so that no unexpected potential appears on the collector.
35. As a developer, I want the picoammeter owned by `Beamline` like every other
    instrument, so that the one-instrument-one-owner invariant holds.
36. As a developer, I want the cup reading published as a typed snapshot, so that
    screens render it rather than converting it.
37. As a developer, I want the acquisition state machine free of Qt and of the
    clock, so that its rules can be tested exactly rather than approximately.
38. As a developer, I want response parsing separated from the polling thread, so
    that the whole path from instrument bytes to rendered number is testable.
39. As a developer, I want the driver-installer machinery removed now that installers
    are no longer shipped, so that the code stops describing a thing that is gone.
40. As a developer, I want the VISA preflight to name the library that is actually
    required, so that a fresh control PC can be brought up from the message alone.

## Implementation Decisions

### Instrument and transport

The instrument is a Keithley 6482 dual-channel picoammeter, reached over GPIB
through a Keysight 82357B USB adapter, driven with SCPI over PyVISA. Only channel 1
is used. Channel 2 is declared in the reading structure and reports absence rather
than a number, so that adding a second cup later is wiring rather than redesign.

Keysight IO Libraries Suite becomes the primary VISA implementation on the control
PC, because the 82357B requires it. The DG1022Z pair and the TDS 2012 are USB-TMC
and are expected to enumerate under it unchanged. **This must be verified on the
bench before any code is written**, and that verification is the first ticket: if
the existing instruments do not enumerate under Keysight VISA, the shape of this
work changes and it is much cheaper to know first.

### The reading

A single query returns the channel 1 current, the instrument's relative timestamp,
and its status word. The status word's over-range bit is the authority on whether a
sample is trustworthy; the application does not attempt to infer over-range from the
magnitude of the value. The instrument signals over-range and unavailable readings
with distinct out-of-band sentinel values, and both are recognised and flagged
rather than being written into the record as though they were currents.

Autoranging is left enabled at all times. The instrument returns amps already
scaled, so the application performs no conversion of any kind on the cup reading —
this is the one-conversion invariant satisfied trivially, and it must stay that way.
The median filter is left off, because the manual documents that it makes
autoranging very slow. Integration is set to one power-line cycle, which supports
the acquiring poll rate with margin.

The configure command that resets the instrument's measurement state is **not**
used, because it also turns both voltage source outputs on. The driver sets the
function and the reading format explicitly and asserts both source outputs off after
connecting. This is a safety requirement, not tidiness: the collector is the thing
those outputs would be driving. It belongs in the module docstring's reasoning
section, not in a comment.

### Layering

A new picoammeter driver joins the hardware layer: instrument protocol and response
parsing, no application state, no Qt. Response parsing is a **pure function** taking
the raw response text and returning a typed reading. It does not live inside the
polling loop — this is what makes the whole path testable, and it is a hard
requirement rather than a preference.

A polling worker follows the existing worker pattern: blocking instrument I/O on its
own thread, communicating only by signals, owning its instrument handle for its
lifetime.

A picoammeter link mixin joins `Beamline`, following the shape of the existing
vacuum link. `Beamline` owns the instrument, publishes a frozen snapshot carrying
`connected` like every other snapshot, and is included in the stepped Connect All
sequence. No widget constructs or tears down the driver.

The acquisition state machine lives in the services layer as a **pure object**: it
receives readings with explicit timestamps and returns run transitions. It does not
import Qt and it does not call the clock. Timestamps are inputs. This is what lets
the debounce and release intervals be tested exactly instead of by sleeping.

The session writer follows the existing CSV writer conventions, flushing after every
row, writing into the application's existing data output location under its own
subdirectory.

### The state machine

Per ADR 0002. A run opens when cup current stays above the arm threshold for the
debounce interval and closes when it stays below the release threshold for the
release interval, with the release threshold the lower of the two. Thresholds and
intervals are configuration in the config layer, not literals in a widget or a
service. Starting values are an arm threshold of 0.5 µA, a release threshold of
0.25 µA, a debounce of 1.0 s and a release interval of 3.0 s.

The machine exposes one value answering "is the cup in the beam?", which today is
derived from current. ADR 0002 decision 6 requires that a future commanded-and-
confirmed cup position replace that value at its source without the run logic
changing. Do not scatter threshold comparisons through the run logic.

Manual force-start and force-stop override the detector completely and are always
available, including while disconnected-then-reconnected.

### Polling rates

Idle ping at 2 Hz; acquiring at 10 Hz. The acquiring rate matches the existing
log-amp screen refresh so the two current views tick together. Both are
configuration.

### The record

One file per application session. Rows are the samples inside acquisition runs.
Columns carry the host timestamp, the instrument timestamp, the current in amps, the
status word, an over-range flag, and the run identifier.

Idle periods are not written as sample rows. They are represented by marker rows: a
run-opened and run-closed marker, a periodic idle heartbeat proving the application
was polling, and markers for instrument disconnection and reconnection. The
requirement this satisfies is that a gap in the record must never be ambiguous
between three different causes. A run-opened marker records the thresholds in force.

### Vocabulary and presentation

The existing "Beam Current" tab is retitled **Slit Currents**. The new tab is
**Faraday Cup**. The terms are already defined in `CONTEXT.md`.

Tab order becomes: Overview, Vacuum, Stepper Motors, Slit Currents, Faraday Cup,
Beam Profiler, Camera, Raster Planner, Function Generators, HV Amplifiers, Dynamic
Adjustment, HV Calibration, Load Characterization. The application already opens on
Overview by title lookup rather than a hardcoded index, so that behaviour is
preserved for free. Reordering is a matter of reordering the single ordered tab
declaration; the stream-consuming set derives from those declarations and does not
need separate maintenance.

Two tests currently hardcode the assumption that the second tab is Beam Current, one
of them by numeric index. Both must be updated with the reorder.

### Retiring the bundled-installer path

Its own ticket, and deliberately conservative in extent. Driver installers are no
longer shipped beside the application; the folder that held them is empty and the
practice was stopped after the antivirus incident. The bundled-installer feature in
the driver preflight module is therefore dead in its entirety — the installer-lookup
helpers, the candidate-directory search, the installer launcher, and the installer
field in the preflight summary — and is removed as one coherent unit. The
capability checks themselves stay: they are alive and useful.

The VISA capability check's guidance text changes to name Keysight IO Libraries
Suite as the required library. The build script's step that copies installers into
the distribution directory is removed. The build specification's commentary about
shipping installers beside the executable is replaced with a short note pointing at
the antivirus incident document.

Nothing else in the preflight module is touched. The goal is removing a dead
feature, not refactoring a working one.

## Testing Decisions

A good test here asserts on **external behaviour**: what a screen renders, what a
file contains, what a run transition does. It does not reach into a widget's private
attributes, and it does not assert that a particular internal method was called.
`CONTEXT.md` and CLAUDE.md §8 both already say this; the existing suite violates it
951 times, and this feature does not add to that count.

ADR 0001 is binding. A failing test is fixed or escalated, never muted — no
expected-failure markers, no weakened assertions, no loosened tolerances, no
narrowed inputs. When a test cannot be made to pass, the escalation path is to
commit the finished work, set the ticket status to blocked, comment on the ticket,
and open a draft pull request.

### The integration seam — one, as high as it goes

A cup feed helper is added to the existing test payload module, alongside the
existing LabJack feed. It injects **raw SCPI response strings** — what the
instrument would actually put on the wire — into a real `Beamline` carrying a real
picoammeter link, wired exactly as the main window wires it.

Everything above PyVISA is therefore inside the test: response parsing, sentinel
handling, status-word interpretation, the snapshot, the acquisition state machine,
the tab render, and the session writer's output. Below the seam sit only PyVISA
itself and the worker's thread mechanics, which nothing in this repo tests through
and which this feature does not change.

Prior art is the existing LabJack feed in the same module, whose docstring explains
why this shape was chosen: the previous approach fed a widget's private method
directly and so exercised a widget-shaped imitation of the production path rather
than the production path. Follow it.

### Direct unit tests

Two pure pieces are tested directly, with no Qt and no hardware, in the manner the
existing pure hardware-layer maths modules are tested:

- **The response parse function**, fed real response strings: a normal reading, an
  over-range sentinel, an unavailable-reading sentinel, a status word with the
  over-range bit set and one without, and malformed input.
- **The acquisition state machine**, fed a synthetic current-versus-time series with
  explicit timestamps. Cases must include: a clean insertion and withdrawal; a
  single spike above the arm threshold shorter than the debounce, which must not
  open a run; a current dwelling between the release and arm thresholds, which must
  not close and reopen a run; a withdrawal shorter than the release interval, which
  must not close the run; force-start while below threshold; force-stop while above
  it; and a disconnection mid-run.

Because timestamps are inputs, none of these tests sleeps.

### Cross-tab agreement

`CONTEXT.md` names cross-tab agreement as an invariant and ADR 0001 records that
nothing in the suite currently tests it. This feature introduces a second current
quantity, which is exactly the situation the invariant guards. Add a test asserting
that slit current and cup current are rendered as **distinct, differently-labelled
quantities** and that the cup reading is not derived from, or conflated with, the
log-amp path.

### The tab reorder

Assert tab identity by title, never by index. The two existing tests that hardcode
the old position must be converted to title lookup rather than renumbered — a
renumbered test will break again at the next reorder.

## Out of Scope

- **Channel 2 of the picoammeter.** Declared in the reading structure, reporting
  absence. No second cup, no ratio or delta display.
- **Any use of the instrument's voltage bias sources**, including secondary-electron
  suppression. The outputs are asserted off. If suppression is wanted later it
  becomes a settable value with its own interlock, and that is a separate design.
- **Commanded cup insertion.** The cup is moved by hand. The LabJack driver in this
  repo exposes no digital or analog outputs today, so actuation is a genuine new
  capability rather than a small addition. ADR 0002 decision 6 keeps the door open;
  this spec does not walk through it.
- **The picoammeter's analog output into the spare LabJack channels.** The spare
  inputs and the instrument's analog outputs are a good fit for sampling the cup
  fast enough to resolve the raster sweep, which GPIB polling cannot do at any
  speed. That is a real future feature and it is not this one. The spare channels
  stay spare and stay excluded from every stream profile.
- **Resolving cup current within a raster sweep**, and any uniformity-versus-position
  analysis that would depend on it.
- **Feeding cup current into beam reconstruction.** The cup is downstream of the
  slits and carries no position information.
- **Cross-checking the log-amp sum against the cup reading** and warning on
  disagreement. This is the most valuable follow-on and it needs bench data to
  establish what disagreement is worth flagging. Not now.
- **Dose or fluence computation.** The application records samples; integration is
  done by the experimenter.
- **The RS-232 path to the picoammeter.** The GPIB adapter is bought and installed.
- **Any change to the LabJack stream profiles, the log-amp path, or slit current
  handling**, beyond retitling the tab that displays it.
- **Refactoring the driver preflight module** beyond removing the dead
  bundled-installer feature.

## Further Notes

**CLAUDE.md §9.5 is now factually wrong** and says so as a live constraint to every
future session: it states that driver installers ship beside the executable. They do
not, and the folder is empty. The developer owns CLAUDE.md and this spec deliberately
does not instruct an implementer to edit it. It should be corrected by hand when the
vendor-removal ticket lands, otherwise the next session will plan around a
constraint that no longer exists. `docs/ANTIVIRUS_FALSE_POSITIVE.md` also describes
the bundled-installer approach as current and would benefit from a closing note.

**The bench verification gates everything.** Nothing in this spec is worth
implementing until Keysight IO Libraries is installed and both function generators
and the oscilloscope are confirmed to still enumerate. Sequence that first.

**There is a natural checkpoint after the driver works.** Whether the instrument
sustains the acquiring poll rate through the GPIB adapter with autoranging enabled
is an empirical question, and the answer sizes the debounce and release intervals.
Do not treat the starting threshold values as settled until a real insertion has
been recorded.

**The weakest part of this design is the current-based inference**, and ADR 0002
says so plainly. It is expected to be replaced by a real position signal rather than
tuned indefinitely. Build it so that replacement is cheap.
