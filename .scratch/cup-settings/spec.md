# Spec: Operator-editable acquisition settings for the Faraday cup

Status: ready-for-agent
Date: 2026-09-24
Related: `docs/adr/0002-cup-acquisition-triggered-by-current.md` (amended 2026-09-24,
read the amendment), `docs/adr/0003-commanded-and-confirmed-cup-position.md`,
`docs/adr/0001-tests-first-and-no-muted-failures.md`,
`CONTEXT.md` (Cup actuation and dose: acquisition settings, settings file, settings
lock, pending change, saved boundary),
`.scratch/cup-actuation/spec.md` (the cycle and dose chain this builds on; all
software tickets 01-11 are done, only bench tickets 12-14 remain)

> ADR 0001 is binding on all test work here: a failing test is fixed or escalated,
> never muted.

## Problem Statement

The Faraday cup acquisition path is driven by numbers that cannot be changed on the
machine that runs it. `rbl/config/cup_config.py` sets `CUP_ARM_THRESHOLD_A = 0.5e-6`,
`CUP_RELEASE_THRESHOLD_A = 0.25e-6`, `CUP_ARM_DEBOUNCE_S = 1.0`,
`CUP_RELEASE_INTERVAL_S = 3.0` and `CUP_SETTLE_WINDOW_S = 1.0`. On the control PC the
application is a PyInstaller build, so every one of those is compiled in. Changing
one means editing source and rebuilding.

That matters because the thresholds have never been checked against a real insertion,
and ADR 0002 already names the failure they produce: a threshold set too high
truncates the start of every insertion, or produces no run at all, and nothing
downstream can recover what was never written. An operator who sees that happening
mid-irradiation currently has no way to correct it.

The settle window is worse in one respect: `CUP_SETTLE_WINDOW_S` is an explicit
placeholder, documented as such, waiting on the bench measurement in
`.scratch/cup-actuation/issues/13-bench-6482-autorange-settle-time.md`. Every
post-settle mean current, and therefore every dose figure, is computed with a number
that is known to be a guess and cannot be changed without a rebuild.

Two limits in the Sampling Cycle panel are arbitrary in the same way. The dwell spin
box stops at 60 s and the period spin box at 86 400 s (`faraday_cup_tab.py`, the
`setRange` calls at the `spn_cycle_dwell` and `spn_cycle_period` construction).
Neither number came from the hardware, and nothing checks the two fields against each
other: `SamplingCycleScheduler.set_period` and `set_dwell` accept whatever they are
given, so a dwell longer than the period is reachable today and the scheduler will
attempt it.

There is also a defect that only appears once thresholds can change. The run record
gets its thresholds from `self.detector.arm_threshold` in
`CupAcquisitionStateMachine`. The detector in service is an `AuthorityDetector`,
whose `__init__` calls `super().__init__()` and so carries the *config defaults* on
itself, while the `CupDetector` it holds in `self._inference` carries its own copy and
is the one that actually compares against the current. Set the inner one and the run
would record the old value; set only the outer one and the comparison would not
change. `CupSessionWriter.__init__` compounds it, writing
`CUP_ARM_THRESHOLD_A` and the other three constants straight into the session
metadata with `setdefault`, regardless of what the detector is using.
`CUP_SETTLE_WINDOW_S` is read the same way at both `compute_insertion_current` call
sites in that file.

## Solution

Give the Faraday Cup tab an Acquisition Settings group holding the arm threshold, the
release threshold and the settle window; put the arm debounce and the release
interval in a settings file that the operator edits with the application closed; and
replace the two arbitrary spin-box limits with rules that relate the fields to each
other.

Settings persist in `~/.config/rbl/cup_settings.json`, written after every committed
edit, validated on load, and owned by the application while it runs. `cup_config.py`
keeps the defaults and remains the only place a default is written down.

The three data-defining values lock while an acquisition run is open or the sampling
cycle is armed. Period and dwell never lock; their edits queue and display as
pending. Stopping a cycle saves the boundary it would have hit, so re-arming can
resume the original schedule instead of restarting the countdown.

Every change lands in the session file: a settings-change marker per edit, disarm and
re-arm markers carrying the saved boundary and the re-arm mode, and the two file-only
values in the session header.

## User Stories

**The beam is weaker than the threshold.** An operator arms the cycle, watches four
insertions produce no runs, and realises the beam is running at 0.3 uA against a
0.5 uA arm threshold. They press Stop Cycle, type 0.1e-6 into Arm threshold, tick
"Keep previous schedule", press Arm. The next insertion happens at the boundary the
cycle would have used anyway, and it produces a run. The session file shows the stop,
the threshold change from 5.0e-7 to 1.0e-7, and the re-arm as resumed.

**The bench measures the settle time.** Ticket 13 finds the 6482 takes 1.6 s to
settle after an autorange. Before the default is changed in `cup_config.py` and
rebuilt, the operator types 1.6 into Settle window between irradiations. The next
insertion summary excludes the right samples, and the row records the window used.

**A long dwell for a weak beam.** A 3 s dwell gives too few post-settle samples at low
current. The operator types 20 into Dwell while the cycle is armed. The field is not
locked, the pending line reads "Dwell 20.0 s pending, applies at next insertion", the
insertion in progress is untouched, and the next one runs for 20 s. Raising Dwell to
400 s against a 300 s period is refused, because dwell plus 4 s must fit inside the
period.

**A typo in the settings file.** The operator hand-edits `cup_settings.json` with the
application closed and leaves `release_threshold_a` above `arm_threshold_a`. On the
next start the tab shows a warning naming the key, the value found, the reason, and
the default used instead. The other keys in the file load normally. Nothing is
silently ignored, and no run is bounded by a value nobody chose.

**An operator tries to edit mid-run.** The cup is in the beam and a run is open. The
three settings fields are greyed and the grey line underneath reads "Locked while a
run is open or the cycle is armed. Edits apply to the next run." Nothing typed is
discarded silently, because nothing can be typed.

## Implementation Decisions

### The settings store is a pure module with no Qt

A new `rbl/config/cup_settings_store.py` holds a frozen `CupSettings` dataclass with
exactly these fields, in these units: `arm_threshold_a`, `release_threshold_a`,
`settle_window_s`, `cycle_period_s`, `cycle_dwell_s`, `arm_debounce_s`,
`release_interval_s`. Defaults come from `cup_config.py` and are written in no other
place.

The module exposes `load_settings()` returning the settings and a list of
`SettingsLoadWarning` records (`key`, `found` as text, `reason`, `fallback`), and
`save_settings(settings)`. `rbl/config/paths.py` gains
`CUP_SETTINGS_STORE: Path = CONFIG_DIR / "cup_settings.json"`.

**Requirement, not preference: validation and file I/O are pure functions with no Qt
on their call path**, so every rule below can be tested by calling a function with a
dict. A rule that can only be exercised by clicking a spin box will not be tested
across the cases that matter.

`load_settings()` never raises. A missing file yields defaults and no warnings. A file
that is not valid JSON yields defaults and one warning naming the file. A key that is
absent, not a number, or outside its rule yields that key's default and one warning;
every other key in the file still loads. This is the opposite of
`rbl/config/persistence.py`, which swallows everything and returns `{}`; that
behaviour is not reused here.

`save_settings()` writes to a temporary file in the same directory and replaces the
target with `os.replace`, so a crash mid-write cannot leave a half-written file.
The written JSON carries a `_README` string key with exactly this value:

    "RBL rewrites this file whenever a setting changes on the Faraday Cup tab. Edit it only while RBL is closed."

`_README` is ignored on load and is not a validated key.

### The rules each value must satisfy

Validated on load and on every edit, with the same function in both places:

- `arm_threshold_a`: 1e-9 <= value <= 1e-3
- `release_threshold_a`: 0 < value < `arm_threshold_a`
- `settle_window_s`: 0 <= value < `cycle_dwell_s`
- `cycle_dwell_s`: value > `settle_window_s`, and
  value + 2 * `CUP_MOVE_CONFIRMATION_TIMEOUT_S` < `cycle_period_s`
- `cycle_period_s`: value >= `cycle_dwell_s` + 2 * `CUP_MOVE_CONFIRMATION_TIMEOUT_S`,
  and value <= 604800.0 (one week)
- `arm_debounce_s`: 0 < value <= 60.0
- `release_interval_s`: 0 < value <= 60.0

The 2 * `CUP_MOVE_CONFIRMATION_TIMEOUT_S` term is the insert and retract moves that
must both complete inside a period. It is computed from that constant, never written
as 4.0.

Cross-field rules are checked against the values in force at the time of the edit. An
edit that fails is refused: the field reverts to its previous value and the reason
appears in the tab's warning line. `CupDetector.__init__` already raises when the
release threshold is not below the arm threshold; the tab checks first so that the
exception is unreachable from the GUI, and the exception stays where it is.

### One setter, one threshold

`CupDetector` gains `set_thresholds(arm_threshold, release_threshold)` which
validates and assigns both. `AuthorityDetector` overrides it to set the inner
`self._inference` thresholds and its own inherited attributes from the same call, and
`CupPositionDetector.arm_threshold` / `release_threshold` become instance attributes
set by the same path rather than class attributes.

**Requirement, not preference: the value a run records is the value the detector
compared against.** After any threshold edit, `RunOpened.arm_threshold` and
`CupRunInfo.arm_threshold` must equal the number used in `CupDetector.update`'s
comparison. This is the defect described in the Problem Statement and it is the single
easiest thing in this work to get wrong, because two attributes with the same name
hold different values today.

`CupAcquisitionStateMachine` is not modified. It keeps reading
`self.detector.arm_threshold`.

### The session writer stops reading constants

`CupSessionWriter.__init__` takes the acquisition settings as parameters and writes
those into metadata, instead of `setdefault`-ing `CUP_ARM_THRESHOLD_A`,
`CUP_RELEASE_THRESHOLD_A`, `CUP_ARM_DEBOUNCE_S` and `CUP_RELEASE_INTERVAL_S`. The
metadata keys keep their current names.

The settle window becomes writer state with a setter, and both
`compute_insertion_current(..., settle_window_s=...)` call sites use it instead of
`CUP_SETTLE_WINDOW_S`. `rbl/hardware/dose_model.py` keeps its existing signature and
its `CUP_SETTLE_WINDOW_S` default; it is passed the live value by its caller.

A new `write_settings_changed(t_host, key, old_value, new_value)` joins the existing
`write_*` family and follows the row conventions already used by
`write_cycle_insertion_skipped` and `write_position_transition`. Two further rows
record cycle lifecycle: `write_cycle_disarmed(t_host, reason, saved_boundary_t)` with
reason in `{"stop", "disarm"}`, and `write_cycle_armed(t_host, mode, next_insertion_t)`
with mode in `{"fresh", "resumed"}`.

### The scheduler owns pending changes and the saved boundary

`SamplingCycleScheduler` stays pure: no Qt, no clock, timestamps in as parameters.
This is an existing requirement of `.scratch/cup-actuation/` and it is not relaxed.

`set_period` and `set_dwell` store a pending value rather than assigning immediately.
`tick(t)` applies a pending period when it crosses a period boundary and a pending
dwell when it begins an insertion. Read-only `pending_period_s` and `pending_dwell_s`
expose the queued values, returning `None` when nothing is queued, so the tab displays
what the scheduler will actually do rather than what the operator typed. A pending
value set while disarmed applies on the next arm.

`disarm()` and `stop(t)` save `_next_insertion_t` as `saved_boundary_t` before
clearing the schedule. `arm(t, resume_at=None)` re-arms at `t + period_s` as it does
today, or at `resume_at` when one is given and `resume_at > t`. `resume_at` in the
past is refused: the scheduler arms fresh and reports which it did, so a stale
boundary can never produce a catch-up insertion. `saved_boundary_t` is kept until a
new `arm()` sets a boundary, and is not persisted across application restarts.

### The tab

A new `rbl/gui/widgets/acquisition_settings.py` holds `AcquisitionSettingsGroup`, a
`QGroupBox` with the three fields, the warning line, the grey lock line, and a
"Reset to defaults" button. It emits `settings_changed(key, old_value, new_value)` and
owns no driver, no file, and no detector. `faraday_cup_tab.py` receives it below the
Sampling Cycle panel with anchored, minimal edits, per the repo's edit discipline.

Fields use `rbl/gui/widgets/inputs.py`, never a bare spin box: thresholds use
`ScientificDoubleSpinBox` in amps, so the number typed is the number stored and
recorded and no microamp conversion exists anywhere; the settle window uses
`QuietDoubleSpinBox`. Each sits in a `unit_row` with its unit beside it.

Lock state is `self.acquisition.is_acquiring or self.cycle.is_armed`, recomputed on
every actuation state update. Locked fields are disabled via `setEnabled(False)`, and
the grey line reads exactly:

    Locked while a run is open or the cycle is armed. Edits apply to the next run.

The Sampling Cycle panel gains a permanent grey line reading
`Changes apply from the next insertion.` and a pending line rendered from the
scheduler's pending values, for example `Dwell 20.0 s pending, applies at next insertion`.

Next to the Arm button sits a `chk_keep_schedule` checkbox labelled
"Keep previous schedule", unchecked by default, enabled only while the scheduler holds
a saved boundary in the future. It greys out on its own as soon as that boundary goes
stale, with the reason in its tooltip. Arming passes `resume_at` only when it is
checked and enabled.

Colours come from `rbl/gui/theme.py` roles, never hex: `MUTED` for the grey lines,
`FAULT` for a rejected edit or a load warning.

### Where the numbers come from at start

`MainWindow` is not involved. The tab loads settings at construction through
`load_settings()`, applies them to the detector, the scheduler and the session writer,
and renders any warnings. Devices are optional, as everywhere in this application: all
of this works with nothing connected.

## Testing Decisions

ADR 0001 is binding. The tests-first CI gate (`scripts/check_tests_first.py`) applies
to every ticket in this set.

### Pure tests, no Qt and no hardware

- The store: every rule above, at and either side of its boundary; a missing file; a
  file that is not JSON; a file with one bad key and four good ones, asserting the
  four load and the warning names the bad one; `_README` ignored; a save followed by a
  load returning the same values; a save that leaves no temporary file behind.
- The detector: after `set_thresholds`, a reading between the old and new arm
  threshold opens or does not open a run according to the new value, and the resulting
  `RunOpened.arm_threshold` equals the new value. Assert on both, in one test, because
  the defect is precisely the two disagreeing.
- The scheduler: a pending period applies at the boundary and not before; a pending
  dwell applies at the next insertion and does not alter an insertion in progress;
  `stop` and `disarm` both save the boundary; `arm(t, resume_at)` with a future
  boundary inserts at that boundary; with a past boundary arms fresh and reports it;
  no catch-up insertion is ever produced. These run over simulated hours in
  microseconds because the scheduler takes its time as a parameter.
- The writer: a settings-change row carries old and new values; the disarm and arm
  rows carry the boundary and the mode; metadata carries the settings that were in
  force, not the constants; an edited settle window changes which samples the
  post-settle statistics include.

### GUI tests through the snapshot pipeline

Offscreen Qt, using `tests/payloads.py` to feed real stream windows through a real
`Beamline`, as the repo requires for anything that puts a value on a screen:

- with a run open, the three fields report `isEnabled() is False` and the lock line is
  visible; with the cycle armed and no run open, the same; with neither, they are
  enabled
- period and dwell remain enabled in all of those states
- a rejected edit leaves the field at its previous value and shows the reason
- the "Keep previous schedule" checkbox is disabled when there is no saved boundary
  and when the saved boundary has passed

### Regression on what already ships

The cup-actuation suite must still pass unchanged. In particular the one-second
insertion regression test from ticket 01, the insertion summary rows from ticket 11,
and the arming preconditions from ticket 10 are not to be adjusted to accommodate this
work. If one of them fails, that is this work breaking a contract, and it is fixed or
escalated, never muted.

Full local gate, in this order:

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile

## Out of Scope

- Fields on the tab for the arm debounce and the release interval. They are file-only
  by decision A2 of the ADR amendment.
- Any change to how the detectors decide cup-in-beam, to the authority rule, or to
  `CupAcquisitionStateMachine`.
- Merging or watching for hand edits to the settings file while the application runs.
  The application owns the file; the file says so.
- Moving any other tab's settings into this store, or generalising it beyond the cup.
- Persisting the saved boundary, the armed state, or any pending change across an
  application restart. Arming stays an explicit operator action after every start.
- Changing `CUP_SETTLE_WINDOW_S`'s default value. That is bench ticket 13 in
  `.scratch/cup-actuation/`, and it is unaffected by this work.
- Bench tickets 12-14 of `.scratch/cup-actuation/`, which remain
  `ready-for-developer`.

## Further Notes

The `.venv` on the control PC builds the exe, so anything that has to be changed
without a rebuild has to live outside `cup_config.py`. That is the whole reason this
work exists, and it is the test for whether a future value belongs in the store: ask
whether a person at the bench, mid-irradiation, could need it different.

The settle window is the one value here that edits history rather than the future: it
changes which already-recorded samples count toward a mean. The raw samples stay in
the session file either way, so any window can be re-applied in analysis later. It is
locked with the thresholds because an insertion whose samples were selected under two
rules is not one measurement, not because the raw data is at risk.
