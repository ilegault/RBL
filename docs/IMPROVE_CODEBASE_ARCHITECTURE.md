# Improving the RBL codebase — prioritized backlog

**Audience:** an AI coding session (Sonnet) working on this repo, one item at a
time. **Read `CLAUDE.md` at the repo root first.**

## How to use this document

- Items are ordered. Tier 0 first — it removes noise that makes every later diff
  unreviewable.
- **One item per commit.** Each item states what to change, why, and how to
  verify. Do not batch unrelated items.
- **Run `pytest` before and after every item.** 1 414 tests exist so a refactor
  can be proved harmless. If an item cannot be made green, stop and report rather
  than deleting or weakening a test.
- **Nothing here should change runtime behaviour** unless the item says so
  explicitly. This is a structural cleanup, not a feature pass. Safety code (the
  ±5 V peak interlock, the vacuum↔HV pressure ladder, the over-current trip
  freeze, the regulation fault detector) is out of scope for restructuring —
  read `docs/AMP_ENVELOPE_AND_HV_SAFETY_PLAN.md` before going near it.
- Where an item says "extract", the goal is *moving* code, not rewriting it.
  Preserve the docstrings verbatim — they are the spec.

## What is already good (do not "improve" these)

The layered package split, `Beamline` as sole instrument owner, the mixin
assembly, frozen-dataclass snapshots, the single volts→physical-units
conversion, the pure-math `hardware/` modules, the `WHY THIS EXISTS` docstring
convention, `tests/payloads.py` driving the production path, and the autouse
fixture protecting the operator's real calibration store. These are the reasons
the codebase is in good shape at 39 k lines. Every item below is meant to
protect them, not replace them.

---

# Tier 0 — repo hygiene (do this first, it is cheap and unblocks review)

## 0.1 Line endings — 164 files show as modified and none of it is real

**Evidence.** `git status` reports 164 modified files, ~50 433 insertions /
48 391 deletions. `git diff rbl/config/persistence.py` shows every line deleted
and re-added identically. `file rbl/main.py` → CRLF; `git show HEAD:rbl/main.py`
→ LF. There is no `.gitattributes` and `git config core.autocrlf` is unset.

**Why it matters.** Nobody can review a real change while the tree looks like
this, `git blame` is one commit away from being useless, and a future session
will either commit 50 000 lines of churn or "helpfully" revert real work.

**Do.**

1. Create `.gitattributes` at the repo root:

   ```gitattributes
   * text=auto eol=lf
   *.bat  text eol=crlf
   *.ps1  text eol=crlf
   *.png  binary
   *.avi  binary
   *.pdf  binary
   *.xlsx binary
   *.exe  binary
   ```

2. Confirm with the user before the next step (it rewrites every file on disk),
   then: `git add --renormalize .` and commit as a single, isolated commit
   titled something like `chore: normalize line endings (no code change)`.
3. Verify the commit is content-free: `git show --stat HEAD` should list the
   files, and `git diff HEAD~1 HEAD --ignore-all-space --stat` should be empty
   apart from `.gitattributes`.
4. Run `pytest` — it must be green, and `build.bat` must still run on Windows
   (that is why `*.bat` is pinned to CRLF).

## 0.2 Untracked new modules are unprotected

**Evidence.** Untracked: `rbl/hardware/bpm_calibration.py`,
`rbl/hardware/slit_raster_model.py`, `tests/test_bpm_calibration.py`,
`tests/test_slit_raster_model.py`, `tests/test_width_levels.py`.

`bpm_calibration.py` (407 lines) is the ms→mm calibration the Beam Profiler
depends on; `slit_raster_model.py` (634 lines) is the slit-limited raster model
the Raster Planner depends on. Both are imported by shipped code and neither is
in git. A clean clone does not build.

**Do.** `git add` all five and commit. Confirm `git clean -ndx` would not delete
anything else the app imports before you do (`grep -rn "slit_raster_model\|bpm_calibration" rbl/ --include='*.py'`).

## 0.3 Editor/session droppings

**Evidence.** Three `*.claude-session.bak` files were committed and are now
staged as deletions. Stale `__pycache__` holds `amp_test_runner.cpython-314.pyc`
and `labjack_poller.cpython-314.pyc` for modules that no longer exist.

**Do.** Complete the deletion of the `.bak` files. Add to `.gitignore`:

```
*.claude-session.bak
*.orig
*.rej
```

Delete stale `__pycache__` directories locally (`find . -name __pycache__ -type d -prune -exec rm -rf {} +`
on the dev machine, or the PowerShell equivalent). Confirm `__pycache__/` is
already ignored (it is) and that no `.pyc` is tracked.

## 0.4 Unused imports — 37 of them

**Evidence** (file: names): `driver.py`: `annotations` · `gui/calibration_tab.py`:
`Qt`, `CAL_PROFILE` · `gui/camera_tab.py`: `sys`, `CSV_INTERVAL_MIN_S`,
`CSV_INTERVAL_MAX_S`, `SEGMENT_SECONDS_CHOICES`, `cv2` · `gui/funcgen_tab.py`:
`QFont`, `QSizePolicy`, `QScrollArea` · `gui/motor_tab.py`: `time`, `Qt` ·
`gui/profiler_tab.py`: `Qt`, `QLineEdit`, `BPM_CAL_EXPECTED_PEAKS`,
`SCOPE_LEVEL_NOISE_GUARD` · `gui/raster_planner_tab.py`: `Rectangle` ·
`gui/vacuum_tab.py`: `QLineEdit`, `VGC_ACTIVE_CHANNELS` ·
`gui/widgets/recording_panel.py`: `sys`, `time`, `QStandardItem`,
`CSV_INTERVAL_DEFAULT_S`, `cv2` · `hardware/scope_worker.py`: `SerialTimeout` ·
`hardware/vgc083_driver.py`: `sys` · `hardware/xgs600_driver.py`: `sys` ·
`services/amp_drive.py`: `AMP_LABELS`, `AMP_CHANNEL_MAP` ·
`services/calibration_runner.py`: `CAL_STEP_KV` · `services/hv_conditioner.py`:
`ma_unclamped` · `services/load_characterizer.py`: `field`, `PEAK_MAX_VOLTS` ·
`services/session_recorder.py`: `json`, `QApplication` ·
`services/video_recorder.py`: `time`.

**Careful — three of these are false positives, leave them alone:**

- `driver.py`'s `from __future__ import annotations` is a compiler directive.
- `camera_tab.py:43` and `recording_panel.py:46` — `try: import cv2 / except
  ImportError: _CV2_OK = False`. The import *is* the availability probe; the
  module is used later as a local `import cv2 as _cv2`. Deleting it breaks the
  no-OpenCV degradation path.

Everything else on the list is genuinely dead. `vacuum_tab.py`'s
`VGC_ACTIVE_CHANNELS` is referenced only inside a comment at line 159 — the real
consumer is `hardware/vacuum_worker.py:74`, so removing the import is safe.

**Do.** Remove the rest. Verify with `pytest` **and** by launching the app —
an import removed from a module that PyInstaller relied on shows up only at
runtime in the frozen build.

---

# Tier 1 — layering violations

`CLAUDE.md` states the rule: `gui → services → state → hardware → config`, one
direction. Four imports break it. Each is a small fix and each one removed makes
the lower layer independently testable.

## 1.1 `config/hardware_config.py` imports the GUI

**Evidence.** `rbl/config/hardware_config.py:18`
`from rbl.gui.theme import SLIT_COLORS`, re-exported at line 260 as
`AMP_COLORS`. So importing the hardware constants pulls in `rbl.gui`, and any
headless consumer of the channel map drags a GUI module with it.

**Do.** Invert it. Colours are presentation: `AMP_COLORS`/`SLIT_COLORS` belong
in `gui/theme.py` alone. Find every `SC.AMP_COLORS` / `hardware_config.AMP_COLORS`
call site (`grep -rn "AMP_COLORS" rbl/ tests/ --include='*.py'`) and point it at
`rbl.gui.theme`. Delete the import and the re-export from `hardware_config.py`.

**Verify.** `python -c "import rbl.config.hardware_config, sys; assert not [m for m in sys.modules if m.startswith('rbl.gui')]"`
must pass. Then `pytest`.

## 1.2 `config/calibration_config.py` imports a hardware driver

**Evidence.** `rbl/config/calibration_config.py:31`
`from rbl.hardware.funcgen_driver import MAX_GEN_VOLTS`, used for the assertion
that `CAL_MAX_KV` undercuts the generator's ceiling.

**Why it matters.** `funcgen_driver` imports `pyvisa`. A config module now needs
VISA installed to import.

**Do.** `MAX_GEN_VOLTS` is a *number*, i.e. config. Move it (and `MAX_AMP_VPP`,
if it is also a bare constant) into a config module — `rbl/config/funcgen_limits.py`
or an existing one — and have `hardware/funcgen_driver.py` import it from there.
Keep the assertion where it is.

**Verify.** `pytest`, plus confirm `import rbl.config.calibration_config` no
longer imports `pyvisa` (`python -c "import rbl.config.calibration_config, sys; print('pyvisa' in sys.modules)"` → `False`).

## 1.3 `services/regulation_response.py` imports a GUI dialog

**Evidence.** `rbl/services/regulation_response.py:31`
`from rbl.gui.regulation_dialog import RegulationFaultDialog`.

**Why it matters.** The regulation *responder* — the thing that reacts to a
confirmed amplifier fault — cannot be tested or reasoned about without Qt
widgets, and the service layer now depends on the layer above it.

**Do.** Invert the dependency with a callback. `RegulationResponder` should take
an `ask_operator` callable (or emit a signal carrying the fault context and
await a `record_answer()` call). `MainWindow` — which already owns all cross-
layer wiring — supplies the implementation that constructs
`RegulationFaultDialog`. Keep the *existing* behaviour identical: same dialog,
same fields, same trip-history record.

**Verify.** `pytest tests/test_regulation_response.py`, and add one test that
drives the responder with a stub callback and asserts the trip-history entry —
proving the service is now headless-testable.

## 1.4 `state/funcgen_control.py` imports `services/ramp_engine.py`

**Evidence.** `rbl/state/funcgen_control.py:27` `from rbl.services.ramp_engine import RampEngine`.
Also: `hardware/scope_worker.py:85` and `hardware/vacuum_worker.py:45` import
`rbl.state.snapshots`.

**Do — and this one needs judgement, not a mechanical fix.** These two are
arguably the layering *model* being slightly wrong rather than the code:

- `snapshots.py` is a set of frozen dataclasses with no behaviour. The honest fix
  is to recognise it as a shared vocabulary rather than "state": move it to
  `rbl/snapshots.py` (or `rbl/types/`) and update the ~20 importers. Then
  `hardware → state` disappears without a single behaviour change.
- `RampEngine` is a slew-limiter with no I/O of its own; it is a *policy*, not an
  orchestration service. Moving it to `rbl/hardware/ramp_engine.py` (it is
  already Qt-signal-based, which `hardware/` workers also are) removes the
  `state → services` edge.

Do **not** attempt both in one commit. Do `snapshots` first (mechanical, wide),
then `RampEngine` (narrow). If either turns out to touch more than ~25 files,
stop and report instead of pushing through.

**Verify after each.** `pytest`, plus re-run the layer check:

```python
# scripts/check_layers.py — add this to the repo as part of item 1.4
import ast, pathlib, sys
ORDER = {'config': 0, 'hardware': 1, 'state': 2, 'services': 3, 'gui': 4}
bad = []
for p in pathlib.Path('rbl').rglob('*.py'):
    L = p.parts[1] if len(p.parts) > 2 else 'root'
    if L not in ORDER:
        continue
    for n in ast.walk(ast.parse(p.read_text(encoding='utf-8'))):
        mods = ([n.module] if isinstance(n, ast.ImportFrom) and n.module
                else [a.name for a in n.names] if isinstance(n, ast.Import) else [])
        for m in mods:
            if not m or not m.startswith('rbl.'):
                continue
            M = m.split('.')[1]
            if M in ORDER and ORDER[M] > ORDER[L]:
                bad.append(f"{p}:{n.lineno} {L} -> {m}")
for b in bad:
    print(b)
sys.exit(1 if bad else 0)
```

Wire it into `pytest` as a test (`tests/test_layering.py`) so the rule is
enforced from then on, not just documented.

---

# Tier 2 — observability

## 2.1 246 `print()` calls, no log file

**Evidence.** 246 real `print()` calls (top offenders: `services/amp_drive.py`
24, `hardware/serial_transport.py` 19, `hardware/vgc083_driver.py` 14,
`config/calibration_config.py` 11, `config/labjack_stream_config.py` 10,
`hardware/labjack_driver.py` 10). Meanwhile 29 modules already use
`logging.getLogger(__name__)`. `rbl.spec` builds with `console=True`, so prints
go to a console window that closes with the app.

**Why it matters.** This is an 8-hour-irradiation-run application. When an
amplifier trips at hour six on the control PC, the diagnostic evidence is a
console scrollback that nobody captured. There is a `logs/` directory in the
repo holding only PNGs — the app writes no log file at all.

**Do.**

1. In `rbl/gui/app.py:main()`, add a `RotatingFileHandler` alongside the existing
   `StreamHandler`, writing to `~/Desktop/RBL_log/logs/rbl.log` (same root as
   every other output — see item 3.2), e.g. 5 files × 5 MB. Keep the console
   handler.
2. Convert `print()` → `log.debug/info/warning/error` module by module, starting
   with the four highest-count files. Map the existing prefixes: a line already
   tagged `WARN` becomes `log.warning`, `ERROR` becomes `log.error`, the rest
   `log.info` or `log.debug` (per-sample chatter → `debug`).
3. `hardware/serial_transport.py`, `vgc083_driver.py`, `xgs600_driver.py` prints
   are largely inside `if __name__ == "__main__"` bench-tool blocks — **check
   before converting**; those should stay as prints.

**Verify.** `pytest`; launch the app, connect nothing, confirm `rbl.log` appears
and captures the driver-preflight results.

## 2.2 `except Exception: pass` — 61 occurrences

**Evidence.** 105 `except Exception:` blocks, 61 of which swallow silently.
`Beamline.shutdown()` alone has six; `MainWindow.closeEvent` has thirteen
try/except-pass blocks in a row.

**The shutdown ones are correct and should stay** — a teardown path must keep
going past a failing instrument. But they should not be *silent*: a T7 that
failed to leave stream mode is exactly the thing you want in the log next time
the app won't reconnect.

**Do.**

1. Add a small helper (in `rbl/state/beamline.py` or a new `rbl/util.py`):

   ```python
   def best_effort(label, fn, *args, **kwargs):
       """Run fn; log and continue on failure. For teardown paths only."""
       try:
           return fn(*args, **kwargs)
       except Exception:
           log.warning("shutdown: %s failed", label, exc_info=True)
   ```

2. Replace the thirteen blocks in `MainWindow.closeEvent` and the six in
   `Beamline.shutdown()` with calls to it. `closeEvent` collapses to a readable
   list of teardown steps.
3. Audit the remaining ~42 silent handlers individually. For each, either add a
   `log.debug(..., exc_info=True)`, or narrow the exception type, or add a
   comment saying why silence is correct. Do not blanket-convert.

**Verify.** `pytest`; close the app with hardware disconnected and confirm the
log shows the teardown sequence rather than nothing.

---

# Tier 3 — fat widgets

The GUI layer is 12 300+ lines and holds the four largest files in the repo.
This is where the codebase will get hard to change.

## 3.1 GUI constructors that are half a file long

**Evidence** (function length in lines):

| | |
|---|---|
| `gui/amp_tab.py:180 __init__` | **499** |
| `gui/calibration_tab.py:155 __init__` | **412** |
| `gui/vacuum_tab.py:143 __init__` | **279** |
| `gui/app.py:65 __init__` | **234** |
| `gui/funcgen_tab.py:509 __init__` | 226 |
| `gui/motor_tab.py:68 __init__` | 172 |
| `gui/logamp_tab.py:49 __init__` | 159 |

**Do.** `profiler_tab.py` already shows the pattern to copy: `_build_readout_box()`,
`_build_widths_box()`, `_build_controls_box()`, `_build_bpm_calibration_box()`…
each returning a `QGroupBox`. Apply it to `amp_tab`, `calibration_tab`,
`vacuum_tab` in that order. Constructor keeps: state init, `_build_*` calls,
layout assembly, signal connections. Nothing else.

`MainWindow.__init__` is a special case: split into `_build_tabs()`,
`_wire_labjack()`, `_wire_calibration()`, `_wire_load_char()`,
`_wire_dynamic_adjustment()`, `_wire_motors()`. **Keep every explanatory comment
with the connection it explains** — those comments are the most valuable prose
in the file.

**Verify.** `pytest` (the GUI tests construct these widgets), plus launch and
click through each affected tab.

## 3.2 Physics inside the Raster Planner widget

**Evidence.** `gui/raster_planner_tab.py` is 1 874 lines and contains
`_steerer_limited()` (56 lines), `_planes_and_drifts()`, `_do_recompute()`
(75 lines), `_channel_capacitance()`, `_check_envelope()`,
`_fill_species_deflections()`. It is the only GUI module besides `video_view.py`
importing numpy.

**Why it matters.** This tab is the research deliverable — its numbers get
defended in a write-up. Math that lives in a widget method cannot be unit-tested,
so the tab's outputs are currently only as trustworthy as the operator's
eyeballs, even though `raster_model.py` and `slit_raster_model.py` next door are
fully tested.

**Do.** Extract the pure computation into a new `rbl/hardware/raster_plan.py`
(or extend `slit_raster_model.py`): a function taking the planner's inputs
(geometry, FWHM, target size, frequencies, capacitances, species) and returning
a plan dataclass. The widget then reads inputs → calls it → renders the result.
Move `_steerer_limited`, the envelope check, and the species deflection table
first; leave the matplotlib drawing (`_draw_beamline`, `_draw_jaw_detail`,
`_draw_dose`, `_draw_envelope`) in the widget — drawing is presentation.

**Verify.** New tests in `tests/test_raster_plan.py` pinning the extracted
functions to values the current tab produces (capture them before refactoring).
Then `pytest`, then compare the tab's on-screen numbers before/after for a couple
of input sets.

## 3.3 `profiler_tab.py` at 1 942 lines does four jobs

**Evidence.** Scope connection + acquisition control, FWHM readout, width-level
table, BPM ms→mm calibration mode (`_build_bpm_calibration_box` at 120 lines plus
`_on_cal_mode_toggled`, `_on_fiducial_pick`, `_sync_fiducial_choices`,
`_on_calibration_state`, `_on_cal_apply`, `_on_cal_clear`, `_redraw_fiducials`),
and session logging.

**Do.** Extract the BPM calibration UI into
`rbl/gui/widgets/bpm_calibration_panel.py` — it is a self-contained mode with its
own state, backed by the already-separate `hardware/bpm_calibration.py`. This is
a lower-risk cut than splitting the tab wholesale. Leave the rest.

**Do this after 3.1 and 3.2**, and only if they went cleanly.

---

# Tier 4 — duplication and inconsistency

## 4.1 `_build_funcgen_map` copied three times

**Evidence.** Identical-purpose helpers at `gui/calibration_tab.py:59`,
`gui/dynamic_adjustment_tab.py:54`, `gui/load_characterization_tab.py:43`.

**Do.** Diff the three (they may have drifted — if so, that drift is a latent
bug worth reporting). Consolidate into one function. It maps amplifier labels to
generator channels, which is beamline knowledge, so it belongs on `Beamline` (as
a method or property) or in `hardware/funcgen_safety.py` beside `CHANNEL_ROLE`.
Prefer `Beamline` — all three call sites already hold one.

**Verify.** `pytest tests/test_calibration_app_wiring.py tests/test_dynamic_adjustment_tab.py tests/test_load_characterization_tab.py`, then the full suite.

## 4.2 Inconsistent tab constructor signatures

**Evidence.** Ten tabs take `(beamline, parent=None)`. `CurrentTab` and `AmpTab`
take `(parent=None)` only and receive everything through `MainWindow` signal
fan-out. `RasterPlannerTab` takes `(parent=None, beamline=None, profiler=None)`
— parent first, dependencies optional.

**Do.** Standardise on `(beamline, parent=None)`, with extra collaborators as
explicit keyword args after `beamline`. `RasterPlannerTab` becomes
`(beamline, profiler, parent=None)`. For `CurrentTab`/`AmpTab`, accept
`beamline` and store it even if the signal fan-out stays as-is — the point is
that a reader can tell what a tab depends on from its signature.

**Do not** change the fan-out pattern itself in this item; that is a bigger
question (see 4.3).

## 4.3 `MainWindow` fan-out vs direct connection — pick one

**Evidence.** Some subscriptions are direct (`beamline.logamps_changed.connect(current_tab.on_logamp_state)`),
others go through a `MainWindow` loop over `self._lj_tabs` calling
`tab.on_labjack_connected(...)`, `tab._on_error(...)` — the latter reaching into
a *private* method on each tab — plus a `hasattr(tab, "on_profile_changed")`
duck-type check at `app.py:432`.

**Do.** Define a small explicit protocol for LabJack-consuming tabs — a
`LabJackConsumer` ABC or `typing.Protocol` with
`on_labjack_connected / on_labjack_disconnected / on_labjack_error / on_profile_changed`
— implement it on all five tabs (a no-op default is fine), rename `_on_error` to
the public `on_labjack_error`, and delete the `hasattr` guard. This is small, and
it makes "what must a tab implement to consume the T7" answerable without
reading `app.py`.

## 4.4 Output paths hardcoded in nine places

**Evidence.** `~/Desktop/RBL_log/...` is constructed independently in
`config/calibration_config.py:305`, `services/conditioning_history.py:14`,
`services/dynamic_adjustment_history.py:15`, `services/profile_logger.py:65`,
`services/session_recorder.py:59` (using `os.path.join`/`expanduser` rather than
`Path`), `services/trip_history.py:24`, `services/vacuum_logger.py:50`. Plus
`~/.config/rbl/` in `config/persistence.py:10` and
`config/load_calibration_store.py:40`.

**Do.** Create `rbl/config/paths.py` with `LOG_ROOT`, `DATA_DIR`, `CONFIG_DIR`
and one accessor per output kind, honouring an `RBL_LOG_ROOT` environment
variable override (useful for tests and for moving output off the Desktop, which
the operator will want eventually). Point all nine at it. Keep the current
default paths byte-identical so nothing moves on the control PC.

**Verify.** `pytest`; then run the app and start a vacuum log, a scope log, and
a session recording, confirming each lands where it did before.

---

# Tier 5 — safety nets

## 5.1 No CI

**Evidence.** No `.github/`. 1 414 tests that only run when someone remembers.

**Do.** Add `.github/workflows/tests.yml`: `windows-latest` (the only supported
target) plus `ubuntu-latest` if the suite passes headless there,
`python-version: ['3.10', '3.13']`, `pip install -r requirements.txt`, `pytest`.
Add `tests/test_layering.py` from item 1.4 so the import rule is enforced.

Note that `requirements.txt` currently pins minimums only
(`numpy>=2.3.0`, `PySide6>=6.11.1`, …). Leave the pins as they are for now, but
add a `requirements-dev.txt` with `pytest` and whatever linter you adopt in 5.2,
and stop shipping `pytest>=9.1.1` in the runtime requirements.

## 5.2 No linter, no type checking

**Do.** Add `ruff` with a deliberately small starting rule set (`E`, `F`, `I`)
and a `pyproject.toml` section; `line-length = 100` matches the existing style.
Run `ruff check --fix` for import sorting only, in its own commit, *after* item
0.1 (otherwise the diff is unreadable). Do not enable auto-formatting of the
whole codebase — the hand-laid alignment in files like `hardware_config.py` and
`app.py` is deliberate and readable, and `ruff format` would destroy it.

Type checking: the codebase has partial annotations. Do **not** attempt a
repo-wide `mypy` pass. Instead add `mypy` in strict mode for `rbl/config/` and
`rbl/hardware/` only (the pure layers), which is achievable and is where a wrong
type actually corrupts a measurement.

## 5.3 Test coverage gaps in shipped logic

**Evidence.** Modules with no same-named test file that carry real logic:
`hardware/scope_worker.py` (881 lines), `hardware/galil_workers.py` (664),
`hardware/serial_transport.py` (571), `hardware/labjack_stream_worker.py` (495),
`hardware/tds2012_driver.py` (627 — partially covered by `test_tds_waveform.py`),
`hardware/vacuum_worker.py` (311), `hardware/ac_metrics.py` (264),
`hardware/amp_trace.py` (213), `services/csv_log_writer.py`,
`services/profile_logger.py`, `state/labjack_link.py`, `state/motor_control.py`,
`state/funcgen_control.py`, `state/scope_link.py`, `state/vacuum_link.py`.

Some are covered indirectly (`test_labjack_stream.py`, `test_scope_acquisition_and_ports.py`,
`test_galil_protocol.py`, `test_stream_payload_stats.py`) — check before assuming
a gap.

**Do, in priority order** (highest value per line of test):

1. `hardware/ac_metrics.py` and `hardware/amp_trace.py` — pure functions, trivial
   to test, and both feed numbers the operator reads as measurements.
2. `services/csv_log_writer.py` — the schema-roll behaviour exists because
   silently dropping a late-connecting instrument's columns was a real data-loss
   bug on 8-hour runs. That deserves a regression test.
3. `state/labjack_link.py`'s window→snapshot conversion — the single most
   load-bearing conversion in the app. `tests/payloads.py` already gives you the
   scaffolding.

Add `pytest-cov` and record the baseline number in this document when you do, so
the next session can see movement.

---

# Tier 6 — smaller sharp edges

- **`OVERVIEW_TAB_INDEX = 4`** (`gui/app.py:62`) is a hand-maintained index into
  a list of twelve `addTab` calls. Replace the whole block with a list of
  `(title, widget)` tuples built once, and look the index up by title. Removes a
  whole class of "the app opens on the wrong tab" bug.
- **`gui/widgets/drag_panel.py`** (494 lines) implements a custom drag-and-drop
  panel layout for the Overview tab. It is the least standard code in the repo
  and has no test file. Before extending it, add tests for the column-major
  reflow; before rewriting it, check whether `QDockWidget` covers the need.
- **`logs/` in the repo root** holds development PNGs, not logs. Rename it
  (`docs/images/` or delete) so it doesn't collide with the runtime log
  directory added in item 2.1.
- **`processing/`** is a one-off analysis script plus its output PNGs and a
  REPORT.md. It has no relationship to the app. Either move it under `tools/`
  with the other bench script, or split it out — right now it reads like part of
  the application.

---

# Suggested order and rough sizing

| Step | Items | Size | Risk |
|---|---|---|---|
| 1 | 0.1 – 0.4 | small | none (0.1 needs user sign-off) |
| 2 | 1.1, 1.2, 1.3 + `tests/test_layering.py` | small | low |
| 3 | 5.1, 5.2 (ruff `E,F,I` only) | small | low |
| 4 | 2.1, 2.2 | medium | low |
| 5 | 4.1, 4.2, 4.3, 4.4 | medium | low |
| 6 | 3.1 | medium | medium — GUI, verify by hand |
| 7 | 3.2 + tests | medium | medium — verify numbers unchanged |
| 8 | 1.4 (snapshots move, then RampEngine) | medium/wide | medium |
| 9 | 5.3, 3.3, Tier 6 | ongoing | low |

Stop and ask the user rather than pushing through if: an item touches more files
than its sizing suggests, a test cannot be made green without changing an
assertion, or the change would alter any interlock, trip, or shutdown behaviour.
