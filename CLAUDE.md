# CLAUDE.md — orientation for AI sessions working on RBL

## Agent skills

### Issue tracker

Issues live as local markdown files under `.scratch/<feature>/`. See `docs/agents/issue-tracker.md`.

### Domain docs

Single-context layout: one `CONTEXT.md` at the repo root, ADRs in `docs/adr/`. See `docs/agents/domain.md`.

RBL is the desktop control/DAQ application for the **Right Beam Line** in the
Ion Beam Laboratory at UW–Madison NEEP. It plans, drives, monitors, and logs an
electrostatic raster beamline: four slit motors, four HV deflection amplifiers,
two function generators, two vacuum gauge controllers, an oscilloscope used as a
beam profiler, and a camera.

**Read this before touching code.** Then read the module docstring of whatever
file you are about to edit — this codebase puts its *reasoning* in docstrings,
not in a wiki, and those docstrings are the actual spec.

---

## 1. Quick facts

| | |
|---|---|
| Language / runtime | Python 3.10+ (dev venv is Windows, `.venv\Scripts\`; `__pycache__` shows cp310 and cp314) |
| GUI | PySide6 (Qt 6), Fusion style, custom light palette |
| Entry point | `python -m rbl.main` → `src/rbl/gui/app.py:main()` |
| Package | `src/rbl/` — ~39 000 lines across 105 modules (src layout) |
| Tests | `pytest` — ~1 414 test functions in `tests/` |
| Build | `pyinstaller rbl.spec --clean`, or `build.bat` (builds + copies `dist/` to a USB drive) |
| Target OS | Windows 10/11 on the control PC. Nothing else is supported. |
| Repo | `master`; remote `origin`. No CI. |

```
# first-time setup (installs rbl package in editable mode into the venv)
.venv\Scripts\activate
pip install -e .

# run the app
python -m rbl.main

# run the tests (Qt runs offscreen automatically, see tests/conftest.py)
pytest                    # whole suite
pytest tests/test_raster_model.py -x -q
```

---

## 2. Layered architecture — the one rule that matters

```
rbl/config/     numbers only. Constants + tiny pure helpers. No I/O, no Qt, no state.
rbl/hardware/   instrument protocol + pure physics/math. No app state, no GUI.
rbl/state/      Beamline: the single owner of every instrument. Converts raw
                volts/counts -> physical units. Publishes typed snapshots as Qt signals.
rbl/services/   long-running orchestration: run state machines, loggers, recorders.
rbl/gui/        widgets. Render snapshots, emit intent. Own no driver, convert no volts.
```

**Imports flow downward only:** `gui → services → state → hardware → config`.
Four upward imports currently exist and are documented as defects in
`docs/IMPROVE_CODEBASE_ARCHITECTURE.md` — do not add a fifth.

### Three invariants that the whole design rests on

1. **One instrument, one owner.** No `QWidget` constructs, holds, or tears down a
   driver. `Beamline` (`rbl/state/beamline.py`) owns the LabJack T7, the Galil,
   both DG1022Z, both vacuum controllers, and the scope. Tabs reach hardware
   through `Beamline` methods or signals. This is why connecting the T7 from the
   Beam Current tab also connects it for the HV Amplifiers tab — there is one
   physical T7.
2. **One conversion.** Raw volts become amps / kV / mA in exactly one place
   (`rbl/state/labjack_link.py`), and are published as frozen dataclasses from
   `rbl/state/snapshots.py`. A widget that converts volts itself is a bug: two
   screens can then disagree about the same physical reading.
3. **Every number has one home.** Tunables live in `rbl/config/*`, never as
   literals inside a widget. `raster_defaults.py`'s docstring explains the
   motivating incident: nine spin-box defaults were buried in a GUI constructor.

---

## 3. The object graph

```
MainWindow (rbl/gui/app.py)
│
├─ Beamline (rbl/state/beamline.py)   ← ONE per process, owns all instruments
│    assembled from mixins, one per instrument, so the class keeps ONE public API:
│      labjack_link.py       T7 handle + stream worker + window → LogAmpState/AmpState
│      funcgen_control.py    both DG1022Z, readback, the ±5 V peak interlock
│      motor_control.py      Galil, poll ingestion, slit moves (published, not just sent)
│      vacuum_link.py        both gauge controllers → VacuumState
│      scope_link.py         TDS 2012 worker → ScopeState
│      hv_interlock_link.py  pressure → permitted-kV ceiling, republished
│
├─ BeamlineSnapshotProvider ─┐
├─ CameraSource ─────────────┤→ SessionRecorder (one per process)
│                            ┘
└─ 12 tabs, each wrapped in a QScrollArea inside a QStackedWidget
     Stepper Motors · Beam Current · HV Amplifiers · Function Generators ·
     Overview · Camera · HV Calibration · Load Characterization · Vacuum ·
     Beam Profiler · Raster Planner · Dynamic Adjustment
```

`MainWindow.__init__` is the wiring diagram. Every cross-tab connection is made
there and carries a comment explaining *why* that connection exists. If you are
adding a feature that spans two tabs, the connection belongs there, not in a tab
reaching for `self.parent()`.

### Signal flow, in one breath

Hardware worker thread → `Beamline` mixin ingests and converts → `Beamline` emits
a typed snapshot → `MainWindow` fans it out (or the tab is connected directly) →
tab renders. Intent travels back the other way: tab emits a request signal →
`MainWindow` or `Beamline` acts → hardware.

Two LabJack channels exist deliberately:
- `logamps_changed` / `amps_changed` — **converted** snapshots, what tabs render.
- `raw_window_ready` — the **unconverted** stream window, for consumers that need
  the original volts (calibration runner, load characterizer, dynamic adjustment).

---

## 4. Threading contract

- All Qt widget work is on the GUI thread. Nothing else touches a widget.
- Blocking instrument I/O lives in `QThread` workers:
  `labjack_stream_worker.py`, `galil_workers.py`, `vacuum_worker.py`,
  `scope_worker.py`, `camera_source.py`, `video_transcoder.py`,
  `gui/widgets/port_picker.py`.
- Workers communicate **only** by Qt signals. Never call a widget method from a
  worker thread.
- A worker owns its port/handle for its lifetime. `SerialTransport` enforces
  one-port-one-lock (`_OPEN_PORTS_LOCK`) so two objects cannot open the same COM
  port.
- LJM stream calls (`eStreamStart/Read/Stop`) happen **only** on the stream
  worker thread.
- Long connect sequences are stepped one instrument per event-loop turn
  (`MainWindow._connect_all_next`, `QTimer.singleShot(0, …)`) so the window
  repaints between instruments instead of looking hung.
- Teardown order matters and is encoded in `Beamline.shutdown()`: stop the stream
  before closing the T7 handle; abort Galil motion before disconnecting; **never**
  disable function-generator outputs on exit — they are meant to hold state.

---

## 5. Where the physics lives

Pure, testable, GUI-free math — this is the research value of the repo:

| Module | Answers |
|---|---|
| `hardware/raster_model.py` | deflection, required drive amplitude, dwell uniformity |
| `hardware/slit_raster_model.py` | the same, when turnaround happens on the **slit jaws** rather than the sample |
| `hardware/beam_reconstruction.py` | beam position/width from four slit currents + four slit edges |
| `hardware/profile_fwhm.py` | FWHM / 1-e² / FWTM from a scope waveform, multi-peak |
| `hardware/bpm_calibration.py` | ms → mm from the BPM's fiducial marks |
| `hardware/load_model.py` | complex load admittance, amplifier operating envelope |
| `hardware/edge_metrics.py` | overshoot, settling, creep, rise time from a step |
| `hardware/ac_metrics.py` | noise-rejecting peak/RMS on a monitor waveform |
| `hardware/regulation.py` | healthy amplifier vs one that stopped following its input |
| `hardware/hv_interlock.py` | permitted plate kV at a given chamber pressure |
| `config/steerer_geometry.py` | the actual steerer + drift geometry of this beamline |

These take plain floats and return plain floats/dataclasses. Keep them that way —
they are the reason the app can be trusted in a defect-study write-up.

---

## 6. Persistence — what the app writes to disk

| Path | Written by |
|---|---|
| `~/.config/rbl/funcgen.json` | `config/persistence.py` (keyed on instrument serial) |
| `~/.config/rbl/load_calibration.json` | `config/load_calibration_store.py` |
| `~/Desktop/RBL_log/data/calibration/` | `services/calibration_writer.py` (CSV + JSON sidecar) |
| `~/Desktop/RBL_log/data/scope/` | `services/profile_logger.py` |
| `~/Desktop/RBL_log/data/vacuum/` | `services/vacuum_logger.py` |
| `~/Desktop/RBL_log/data/*.jsonl` | `trip_history`, `conditioning_history`, `dynamic_adjustment_history` |
| `~/Desktop/RBL_log/logs/` | `services/session_recorder.py` (CSV + video segments) |

CSVs flush after every row on purpose: a 12-hour drift run that dies at hour 11
must leave 11 hours of usable data. `csv_log_writer.py` rolls the schema when a
new instrument connects mid-run rather than silently dropping its columns.

---

## 7. Conventions to follow

- **Module docstrings explain WHY, with a `WHY THIS EXISTS` section.** Not what
  the code does — the reader can see that. Record the bug, the constraint, or the
  physical fact that forced the design. Match this style; it is the single best
  thing about the codebase.
- **`config/theme.py` roles, not hex codes.** A value is `OK` / `WARN` / `FAULT`
  / `MUTED` first. If amber means "approaching the peak rating" on one tab, it
  must mean that everywhere.
- **Frozen dataclass snapshots carry `connected`** so a view can tell "no data
  because it isn't hooked up" from "no data yet".
- **Numeric entry uses `gui/widgets/inputs.py`**, never a bare `QDoubleSpinBox` —
  the plain widget re-interprets its text on every keystroke and mangles typed
  values. Combo boxes and spin boxes are the no-scroll variants so a stray mouse
  wheel over the tab cannot change a setpoint.
- **Devices are optional.** Every tab must work with nothing connected. The
  Raster Planner and all pure-math paths run with no hardware at all.
- **Safety features are load-bearing, not decoration.** The ±5 V peak interlock,
  the vacuum↔HV pressure ladder, the over-current trip freeze, the regulation
  fault detector. Do not "simplify" one without reading its docstring and
  `docs/AMP_ENVELOPE_AND_HV_SAFETY_PLAN.md`.

---

## 8. Testing

- `tests/conftest.py` sets `QT_QPA_PLATFORM=offscreen` before any PySide6 import
  and has an **autouse** fixture redirecting the load-calibration store to a temp
  file — a test once wrote a fabricated capacitance into the operator's real
  store, and the Raster Planner labels that value "measured".
- `tests/payloads.py` builds real `LabJackStreamWorker` windows and feeds them
  through a real `Beamline`, so tests exercise the production path rather than
  poking private methods on a widget. **Use it** for anything that puts a voltage
  on a screen.
- Prefer testing the pure `hardware/` math directly; GUI tests should assert on
  what the snapshot pipeline produced, not on internal widget state.

---

## 9. Known traps

1. **Line endings.** There is no `.gitattributes` and `core.autocrlf` is unset.
   The working tree is CRLF, the index is LF, so `git status` currently reports
   ~164 files modified with ~50 000 insertions that are pure line-ending churn.
   **Do not interpret that as real uncommitted work, and do not commit it.** See
   `docs/IMPROVE_CODEBASE_ARCHITECTURE.md` item 0.1 for the fix.
2. **`OVERVIEW_TAB_INDEX = 4`** in `app.py` is a hand-maintained index into the
   `addTab` calls. Insert a tab above it and the app opens on the wrong screen.
3. **Split view reshuffles the stack.** Right-clicking a tab *removes* its scroll
   area from `_outer_stack`, so stack indices shift. `_stack_index()` exists for
   exactly this; use it rather than assuming tab index == stack index.
4. **Stale `__pycache__`** contains modules that no longer exist
   (`amp_test_runner`, `labjack_poller`). Don't be misled by a grep hit in a
   `.pyc`; always restrict to `--include='*.py'`.
5. **PyInstaller `vendor/`.** Driver installers ship *beside* `RBL.exe`, not
   inside the archive — bundling them got the app flagged as a dropper. Read the
   long comment in `rbl.spec` before changing it, and see
   `docs/ANTIVIRUS_FALSE_POSITIVE.md`.
6. **Print vs logging.** 246 `print()` calls and 29 modules using `logging`
   coexist. The exe builds with `console=True`, so prints are visible but nothing
   lands in a log file. Prefer `logging.getLogger(__name__)` in new code.

---

## 10. Where the prior reasoning is written down

In-repo:
`docs/AMP_ENVELOPE_AND_HV_SAFETY_PLAN.md` (the phased safety plan the calibration,
conditioning, ramp, regulation and dynamic-adjustment features implement — phase
numbers referenced throughout the code point here),
`docs/UI_FIXES_CAPTURE_QUALITY_AND_RASTER_RESTRUCTURE.md`,
`docs/SCOPE_WAVEFORM_BLANK_AND_IDENTIFY_SPAM.md`,
`docs/ANTIVIRUS_FALSE_POSITIVE.md`,
`docs/IMPROVE_CODEBASE_ARCHITECTURE.md` (the prioritized refactor backlog).

In the Claude project "Right Beam line": session findings on beam-width ladders,
slit-limited raster, scope FWHM, BPM ms→mm calibration, raster planner drift and
rebuild, steerer geometry decision, JSON logging rewrite, and the instrument
manuals (Galil DMC-41x3, DG1000Z, EEL5000, VGC083, XGS-600, XY steerer). Search
the project before re-deriving a number.

---

## 11. Working agreement for AI sessions

- Read the module docstring before editing the module. It usually already answers
  "why is it like this".
- Run `pytest` before and after. 1 414 tests exist precisely so a refactor can be
  proved harmless.
- When you change behaviour, update the docstring's reasoning — a stale *why* is
  worse than none.
- New tunable number → `rbl/config/`. New instrument protocol → `rbl/hardware/`.
  New long-running run → `rbl/services/`. New screen → `rbl/gui/`, wired in
  `MainWindow.__init__`.
- Don't leave `.claude-session.bak` files in the tree; three were committed once
  already.

### Fix or escalate — never mute a failing test

Binding rule, from `docs/adr/0001-tests-first-and-no-muted-failures.md` (read it
before touching a failing test): **a failing test is fixed or escalated, never
muted.** That means never marking it `xfail`, never deleting or weakening the
assertion, never loosening a tolerance, and never narrowing its inputs until it
happens to pass. Those are all the same move — making the test stop reporting the
problem instead of fixing the problem — and ADR 0001 exists because that move was
tried once and produced eleven false "expected failures," three of which cited a
calibration ladder step that does not exist in the codebase.

**When you cannot fix a failing test, stop and escalate. Do not guess at a domain
decision and do not work around it.** The escalation procedure has four steps:

1. **Commit your finished work to the branch.** Whatever is done and correct so
   far is preserved, not lost.
2. **Set the ticket's own `Status:` line to `blocked`.**
3. **Append a comment under the ticket's `## Comments` heading**: what you
   attempted, what failed, and what needs a human decision. (See
   `docs/agents/issue-tracker.md` for the comment convention.)
4. **Open the pull request as a draft.** Master is not touched.

The ticket file travels with the branch, so the draft PR plus its failing CI run
*is* the report — a planning session can read the ticket directly off the branch
with no separate handoff. This is also why the report must be written into the
ticket file itself, in `.scratch/`, and not anywhere under `.claude/`: that
directory is gitignored in this repo, so anything written there never gets
pushed and no reviewer — human or AI — will ever see it.

<!-- ACTIVE-PLAN:START -->
## Active implementation plan

_Written by the planning model on 2026-09-08 03:57. Implement this. If something in it is wrong, say so before changing course._

# Active work: test suite overhaul

This is a **pointer**, not the work. The work is a ticket set.

- Spec: `.scratch/test-suite-overhaul/spec.md`
- Decision record: `docs/adr/0001-tests-first-and-no-muted-failures.md` — **binding, read it first**
- Tickets: `.scratch/test-suite-overhaul/issues/01…18`
- Tracker conventions: `docs/agents/issue-tracker.md`

## Next up

Both are unblocked; either can start.

- **01 — CI executes the test suite and the type gate is layered.** Land this
  first regardless. It establishes the baseline test counts that every later
  ticket measures against, and until it exists every later ticket is guesswork.
- **02 — Blocked-work protocol written down; stale plan deleted.** Independent,
  no application code.

## Rules for working this set

- Do not start a ticket whose `Blocked by:` line names an unfinished ticket.
  Work the frontier: any ticket whose blockers are all done.
- **11 is a review gate.** It presents the audit verdicts and stops for the
  developer. Nothing after it starts until they sign off. No test is deleted
  and no application source is changed before that.
- A failing test is fixed or escalated, never muted. `xfail` is not a tool for
  greening a build. The escalation path — commit to branch, ticket
  `Status: blocked`, comment on the ticket, draft PR — is in ADR 0001 decision 3
  and in ticket 02.
- The developer does not run the suite locally. Every ticket is verifiable from
  CI output alone.
<!-- ACTIVE-PLAN:END -->
