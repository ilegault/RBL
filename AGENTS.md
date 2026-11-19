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
| Repo | `master`; remote `origin` = `github.com/ilegault/RBL`. CI on push and PR. |

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
   Slit Currents tab also connects it for the HV Amplifiers tab — there is one
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
     Overview · Vacuum · Stepper Motors · Slit Currents · Beam Profiler ·
     Camera · Raster Planner · Function Generators · HV Amplifiers ·
     Dynamic Adjustment · HV Calibration · Load Characterization
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
5. **PyInstaller `vendor/`.** Bundled driver installers have been retired
   completely — bundling them inside or shipping them beside the app got the app
   flagged as a dropper. System drivers are installed once on the control PC.
   See `docs/ANTIVIRUS_FALSE_POSITIVE.md`.
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
- Run the tests your change touches while you work, and every gate command before
  the PR, with `pytest` limited to those tests. CI runs all 1 414 tests on the PR,
  merged with the latest `master`; that run is what proves a refactor harmless.
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

### Tests-first CI check and documented escape

CI enforces that any pull request or commit modifying application code under `src/` must also modify tests under `tests/` (`scripts/check_tests_first.py`). Changes touching only documentation (`docs/`), build scripts (`scripts/`, `tools/`), CI configurations (`.github/`), or tests (`tests/`) pass automatically.

A change under `src/` must also touch `tests/`. A `.py` file whose code is unchanged
once docstrings and comments are removed does not count as a change. Otherwise the
escape is explicit and visible, and its reach depends on where it is written: a tag
(`[no-test-needed: <reason>]`, `[tests-exempt: <reason>]` or `[skip-test-gate]`) in
a commit message covers only the files that commit changed, and a file is exempt
only when every commit that changed it is tagged; the same tag in the PR title or
body, or the `tests-exempt` / `skip-test-gate` label, covers the whole PR, and CI
re-runs when the PR text or labels change. The check fails, never passes, when it
cannot work out what changed.

### Implementing a ticket — the protocol

This repo is worked by more than one agentic tool, and not all of them have slash
commands. Anything an implementer must obey lives here, in this file, because this
file is the only thing every tool reads. A rule that lives only in a command binds
only the tool that has that command.

1. Read the `ACTIVE-PLAN` block below. It names the ticket set and which tickets are
   unblocked. Read the ticket file, and read every ADR it references.
2. **Work the frontier.** Never start a ticket whose `Blocked by:` line names an
   unfinished ticket. If everything unblocked is a developer bench task, say so and
   stop — do not claim it and do not invent work around it.
3. Set the ticket's `Status:` line to `in-progress` before you start, and to `done`
   when it lands. Use exactly those two words. Three spellings of "finished" were
   used across the last ticket set and the frontier stopped being machine-readable.
4. One ticket per branch. One pull request per ticket. Never commit to `master`.
5. Before you open the PR, run every gate command, with `pytest` limited to the
   tests your change touches. CI runs the full suite on the PR, merged with the
   latest `master`, and watching it is part of the ticket (rule 10). A refactor
   that cannot be proved harmless is not finished.
6. **A failing test is fixed or escalated, never muted.** No `xfail`, no deleted or
   weakened assertions, no loosened tolerances, no narrowed inputs until it passes.
   These are all the same move. `docs/adr/0001-tests-first-and-no-muted-failures.md`
   is binding and explains what happened the one time it was tried.
7. When you cannot fix a failing test, escalate in four steps: commit the finished
   work to the branch; set the ticket's `Status:` to `blocked`; append what you
   attempted, what failed, and what needs a human decision under the ticket's
   `## Comments` heading; open the pull request as a **draft**.
8. **The escalation report goes in the ticket file, under `.scratch/`.** Never under
   `.claude/` — that directory is gitignored, so anything written there is never
   pushed and no reviewer, human or agent, will ever see it.
9. Update the module docstring's reasoning when you change behaviour. A stale *why*
   is worse than none.
10. **After pushing, watch CI and fix what it finds** — up to two fix-and-push
    cycles, then escalate. See the section directly below; it is binding.
11. **The claim commit carries no test-gate escape tag, and no PR title or body ever
    quotes one.** A claim commit changes only the ticket file under `.scratch/`, which
    the tests-first gate already passes, so a tag there exempts nothing and only makes
    the engine's integrity gate HOLD the branch for a human. Worse, an escape tag
    written anywhere in a PR title or body, even inside a sentence reporting on it,
    exempts the **whole PR** from the tests-first gate (ticket 84, PR #177). When a
    report must mention one, name it in words ("the claim commit's escape tag") and
    never type the bracketed tag itself.

### Watching CI, and fixing what it finds

**Do not open the pull request and walk away.** The session that wrote the code is
by far the cheapest place to fix it: it still holds the ticket, the ADRs, the diff
and its own reasoning. The same failure found an hour later costs a fresh session
that has to re-read all of that before it can even read the error message.

After pushing the branch and opening the PR:

```
gh pr checks --watch --fail-fast
```

`--watch` blocks until every check resolves; `--fail-fast` returns as soon as one
fails. The full gate here is four checks on a Windows runner and the suite is
~1 400 tests, so a complete run takes many minutes. Wait it out — a slow check is not a reason to stop watching.

If `gh` is not installed or not authenticated, install it (`winget install
GitHub.cli` on Windows, then `gh auth login`). If you genuinely cannot reach it,
**say plainly that CI was not observed.** Never report a run green that you did
not watch resolve.

#### When it goes green

Tick the acceptance criteria individually, set the ticket's `Status:` to `done`,
commit the ticket file, report what landed, and stop. Do not start the next ticket.

#### When it goes red

**Read the failure before theorising about it.**

```
gh run list --branch "$(git branch --show-current)" --limit 1 --json databaseId --jq '.[0].databaseId'
gh run view <run-id> --log-failed
```

`--log-failed` prints only the steps that failed. **Never pull the full run log
into context** — on this repo that is thousands of lines of passing tests, and it
tells you nothing the failed-step output does not.

Then fix and push again, under a budget.

#### The budget: two fix-and-push cycles, then escalate

Not three. Not "one more, I think I have it this time." A third attempt at the same
failure means the diagnosis is wrong, and the cost of being wrong a third time is
higher than the cost of a person spending five minutes on it.

Each cycle is: read the failed-step log, diagnose the **root cause**, fix it, commit
with a message naming what the failure actually was, push, watch again.

#### Classify the failure, and say which you concluded

State your classification in the commit message. A fix whose reasoning is not
written down is indistinguishable from a guess that happened to work.

- **A real defect** — the test is right, the code is wrong. Fix the code. This is
  the test doing its job; it is good news.
- **A harness defect** — the test asserts something the code never promised, or
  models the world wrong (a fake that does not behave like the real thing, a
  fixture that leaks). Fix the test, and say in the commit message why the old
  assertion was wrong. **This is the category an agent under pressure abuses**, so
  the bar is: you can state what the test should have asserted instead, and it
  still fails if the behaviour is wrong.
- **An environment difference** — passes locally, fails in CI. Windows path separators, CRLF vs LF, a Qt
  timing assumption that does not hold under `-n auto` on a loaded runner, a
  missing entry in `requirements-dev.txt`, a test that depended on a file the
  runner does not have. Fix
  the cause. "It's environmental" is a diagnosis, not an excuse, and it is never a
  reason to skip or condition the test on CI.

#### The loop makes muting more tempting, not less

You can now watch the build go red and push again thirty seconds later. That is
precisely the situation `docs/adr/0001-tests-first-and-no-muted-failures.md` was
written for. Under time pressure the cheapest-looking move is to edit the test
rather than the code, and it is the one move that is always forbidden: no `xfail`,
no `skip`, no deleted or weakened assertion, no loosened tolerance, no narrowed
input, no `try/except` swallowing the error the test existed to surface.

**If you are editing a test so that it stops reporting a problem you have not
fixed, you are escalating, not fixing.** Stop and escalate.

#### When the budget runs out

Escalate in the four steps — commit the finished work, set the ticket's `Status:`
to `blocked`, append to `## Comments`, convert the PR to a draft
(`gh pr ready --undo`). In the ticket comment, record:

- what CI said, quoted from the failed-step log, not paraphrased
- what you concluded each attempt, and what each attempt changed
- why you think it is still red
- what decision you need from a human

The draft PR plus its failing run **is** the report. Nothing else needs writing,
and nothing goes anywhere a reviewer cannot reach.

#### A red check you did not cause is not yours to fix silently

If `master` is already failing, or a check fails for a reason unrelated to your
diff, say so and do not bury the fix inside your ticket's branch. A refactor with
someone else's bug fix smuggled into it cannot be reviewed.

<!-- ACTIVE-PLAN:START -->
## Active implementation plan

_Written by the planning model on 2026-11-18 23:09. Implement this. If something in it is wrong, say so before changing course._

# Active work: slit homing on the release edge, and the switch map (set 14, new)

This is a pointer, not the work.

## Set 14 (new): slit homing on the release edge, and the switch map

- **Spec:** `.scratch/slit-homing-release-edge/spec.md`
- **Binding:** `docs/adr/0009-slits-homed-on-the-release-edge.md` (new: read it first),
  `docs/adr/0001-tests-first-and-no-muted-failures.md`.
- **Glossary (`CONTEXT.md`, Slit motion, edited by the planner, committed by the developer, no
  ticket):** new **Reverse limit**, **Forward limit**, **Approach speed**, **Switch map**;
  **Home pass** redefined (drive onto the switch, step out to the release point, the *edge*);
  **Home switch** notes it trips with the reverse limit.
- **Tickets:** `.scratch/slit-homing-release-edge/issues/135...142`. 135–141 are agent
  tickets, all `Auto-merge: yes`. 142 is `ready-for-developer` (bench): an agent must not
  claim it.
- **Tracker conventions:** `docs/agents/issue-tracker.md`
- **Why:** since ticket 123 every home and edge-return pass fails with stop code 3, because
  each slit's home switch and reverse limit trip at the same point and the controller's `HM`
  can never finish. ADR 0009 replaces `HM` with a release-edge pass.

### Next

- **135** and **141** are unblocked and touch different code. Start with **135**: it makes
  homing work again and adds `tests/sim_slit.py`, the simulated slit 136, 137 and 139 build on.
  141 (status label) can run in parallel with it.

### Dependency order

```
135 ─┬─ 136 ─┬─ 137 ─┬─ 138 ─┐
     │       └───────┘       ├─ 140 ─┐
     └─ 139 ─────────────────┘       ├─ 142 (developer, bench)
141 ─────────────────────────────────┘
```

(137 is blocked by 135 and 136; 138 by 136 and 137; 140 by 138 and 139; 142 by all of 135–141.)
`src/rbl/gui/motor_tab.py` is edited by 135, 136, 137, 138, 140 and 141; the chain keeps all but
141 apart. If 141 and another `motor_tab.py` PR are open together, land one and rebase the other.

### Requirements that are not preferences

- 135: routine tests drive the **simulated slit** (`SimGalil` in `tests/sim_slit.py`), which
  has a real position, a shared home/reverse switch with hysteresis, approach overshoot and
  injectable lost steps. Scripted mock return values cannot show where zero lands, so they
  are not a substitute.
- 135: zero is defined only when the two passes' edges agree within
  `HOME_EDGE_AGREEMENT_COUNTS` (4); otherwise no `define_zero` call at all. A creep of 400
  counts without a release fails with "check the sensor". Stop code 3 on the approach is
  expected and is never treated as a failure by itself.
- 135/137/139: the edge-return test and the switch map never call `define_zero`.
- 139: the switch-map edge-finding (`find_switch_edges`, `switch_map_summary`) is a pure
  module with no I/O, and the routine never commands an inward step while the reverse limit
  reads tripped.
- 138: the tests named on its `Deletes tests:` line are the only ones removed; every other
  test that referenced a removed name is rewritten in place under its own name.
- Every ticket: only the tests on its own `Deletes tests:` line may be deleted.

### Not in this set

Opening or rewiring the switch box; CN, MT, BL/FL or direction changes; recalibrating
`MM_ZERO_OFFSET` (a follow-up from bench ticket 142); renaming the edge-return CSV's
`pass_speed_cps` column; encoders; saving the approach speed; anything on the Overview.

---

## Earlier sets: only developer bench tickets remain open

Every agent ticket in sets 1–13 is `done`. These `ready-for-developer` tickets are still open;
an agent must not claim them:

- Set 13 slit motion: `.scratch/slit-motion/issues/134-bench-encoders-rotation-and-edge-return.md`
  (its edge-return and pass-speed items are replaced by 142; see its 2026-11-18 comment)
- `.scratch/picoammeter-connect/issues/119-bench-lab-pc-reaches-the-6482.md`, `120-bench-bpm-scope-answers-on-serial.md`
- `.scratch/axis-offset-and-vpp/issues/112-bench-check-offset-send-and-apply.md`
- `.scratch/zwo-open-diagnostics/issues/104-bench-zwo-open-three-ways.md`
- `.scratch/camera-layout-and-draft-roi/issues/97-bench-zwo-roi-and-compact-panel.md`
- `.scratch/cup-target-uncertainty/issues/88-bench-poll-rate-and-correlation.md`
- `.scratch/zwo-capture/issues/78-bench-frame-rate-and-bit-depth.md`, `79-bench-end-to-end-with-the-real-camera.md`, `80-bench-lab-pc-deployment.md`
- `.scratch/current-monitor-scale/issues/60-bench-archive-and-clamp-at-dial-50.md`
- `.scratch/amplifier-load-and-spikes/issues/52-bench-campaign.md`
- `.scratch/cup-actuation/issues/12-bench-wiring-and-status-decoding.md`, `13-bench-6482-autorange-settle-time.md`, `14-bench-end-to-end-bringup-and-lag.md`
- `.scratch/faraday-cup-reader/issues/10-existing-instruments-under-keysight-visa.md`
<!-- ACTIVE-PLAN:END -->
