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

_Written by the planning model on 2026-11-23 23:12. Implement this. If something in it is wrong, say so before changing course._

# Slits homed with HM, as NEC specifies (slit-homing-hm), then the ZWO bench follow-ups

This is a pointer, not the work. Two efforts are open; do the homing tickets first.

## 1. slit-homing-hm (first)

- **Spec:** `.scratch/slit-homing-hm/spec.md`
- **Binding ADRs:** `docs/adr/0010-slits-homed-with-hm-as-nec-specifies.md` (supersedes 0009), `docs/adr/0001-tests-first-and-no-muted-failures.md`
- **Glossary:** `CONTEXT.md` — Slit motion terms rewritten for HM homing (pre-home move, homing speed, home pass, stop position, release edge, approach speed)
- **Tickets:** `.scratch/slit-homing-hm/issues/175`–`180`
- **Tracker conventions:** `docs/agents/issue-tracker.md`

**Next:** 175 (the only homing ticket with no blockers; everything else waits on it). Then 176, then 177 and 178 in parallel.

Dependency graph:
- 175 driver `home` / `define_encoder_zero`, constants, `SimGalil.home` → 176
- 176 HM homing routine, workers, one Home button (blocked by 175; carries a 31-test `Deletes tests:` line) → 177, 178
- 177 Auto-Home All texts and failure dialog listing every failed axis (blocked by 176) → 179
- 178 edge-return records stop and edge; new CSV columns (blocked by 176) → 179
- 179 labelled stop/edge summary lines; Approach speed box moves to the edge-return box (blocked by 177, 178)
- 180 bench — ready-for-developer, Runner: developer, not for agents (blocked by 175–179). Bench ticket 142 is retired.

Requirements an implementer would quietly treat as preferences:
- Every switch and stop-code read in a home move happens after `wait_at_rest`.
- Homing never reads the Approach speed box; it sets only 900 (HM) and 1800 (pre-home move, restore).
- `back_out_of_home` and `HOME_START_BACKOFF_*` stay: the switch map must work exactly as today.
- `approach`, `creep_to_release(self)` and the routine's `approach_speed` argument survive 176 for the edge-return test and are removed in 178, not before.
- Exact messages and UI strings are given in the tickets; use them verbatim.

Deliberately not done: encoders and `TP`/`YS`/`QS`/`OE`; changing the 4-count limit, `CN`, `MM_ZERO_OFFSET` or `STEPS_PER_MM`; the switch map; continuing Auto-Home All past a failed axis; fixing the duplicate test definitions in `tests/test_switch_map_tab.py`.

## 2. zwo-bench-followups (after homing)

- **Spec:** `.scratch/zwo-bench-followups/spec.md` (binding: ADR 0008, ADR 0001)
- **Tickets:** `.scratch/zwo-bench-followups/issues/158`–`166`
- **Next there:** 162 (heads 162 → 163 → 164 → 165); 158, 159, 160, 161 have no blockers. 166 is the developer's bench check.
- Requirements: SER `LittleEndian` stays 0; slider mappings and auto-adjust maths are pure functions; the Capture Process only suggests and the application applies through `set_zwo_settings` / `set_zwo_preview`; the `--fake-no-frames` and `--fake-red-scale` seams are required; white balance is Preview only.

Still open elsewhere: 174 (cup-status-fix bench, developer) and 152 (capture-hello-on-ci, ready-for-agent).
<!-- ACTIVE-PLAN:END -->
