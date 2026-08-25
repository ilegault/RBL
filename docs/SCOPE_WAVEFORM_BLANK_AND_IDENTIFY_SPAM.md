# Profiler: blank waveform after every shot, and `identified` every 5 s

Two separate defects, found by reading the code on 25 Aug 2026. Neither needs
hardware to reproduce in a test. Fix them in the order below — the first is
the one that matters.

> **STATUS: both fixed, 25 Aug 2026.** Bug 1 in `rbl/gui/profiler_tab.py`
> (split cache: `_last_state` for the link, `_last_measured` for the beam);
> Bug 2 in `tds2012_driver.py` (identify demoted to DEBUG) and
> `scope_worker.py` (`_KEEPALIVE_S` 5 s → 15 s, ping logs only when the
> identity string changes). Regression tests live in
> `tests/test_scope_acquisition_and_ports.py`
> (`TestIdleSnapshotsDoNotEraseTheMeasurement`) and
> `tests/test_tds_waveform.py` (`TestIdentifyIsQuiet`). The bench check
> below — continuous mode draws, single shot does not — was *not* run
> before the edit; run it only if the symptom survives this fix, because it
> would mean the diagnosis was wrong.
>
> Left deliberately alone: the scope's front panel reads **Flow control:
> None** while `scope_worker._connect()` opens the port with `rtscts=True`.
> Short queries survive that mismatch; a 2500-point `CURVE?` may not. If
> transfers start truncating, set the scope's RS-232 flow control to
> Hard(RTS/CTS) before touching the code.

**Symptoms as reported from the beam line**

1. Press **Take shot**, the FWHM history plot gains a point, the numbers are
   right — and the *Waveform (last acquisition)* pane stays blank.
2. `rbl.hardware.tds2012_driver` logs `tds2012: identified — ID TEK/TDS 2012…`
   every 5 seconds, drowning the terminal.

---

## Bug 1 — the idle snapshot overwrites the measurement before the plot timer sees it

### What is actually happening

`ProfilerTab` keeps **one** cached snapshot, `self._last_state`, and redraws
off it on a timer. But `ScopeWorker` emits **two different kinds** of
`ScopeState`: one carries a measurement (trace, peaks, widths), and one is a
bare status ping (`idle=True`, no trace, no peaks). The tab stores both in
the same slot, and the status ping arrives *first*.

Sequence for a single shot — `rbl/hardware/scope_worker.py`, `_run_loop`:

1. The shot is acquired and measured. `waveform_ready` emits a full
   `ScopeState`: `corrected_downsampled` populated, `peaks` populated,
   `idle=False`.
2. **Single-shot mode does not wait.** The rate-limit sleep at the bottom of
   the loop is inside `if cfg["continuous"]:`, so the loop immediately comes
   round, finds no shot pending, enters the idle branch, and — because
   `was_idle` was set `False` before acquiring — the `if not was_idle:` guard
   passes and it emits an **idle `ScopeState` within milliseconds**:
   `connected=True, idle=True`, and every data field at its dataclass default,
   i.e. `corrected_downsampled == []`, `peaks == []`.
3. `ProfilerTab._on_scope_state` runs for both. It does
   `self._last_state = state` unconditionally, so the idle snapshot
   **replaces** the measurement. `_plot_dirty` was set `True` by step 1 and is
   not cleared by step 2 (`if not state.idle:` only ever *sets* it).
4. Up to **1000 ms later** (`self._plot_timer` interval) `_redraw_plots` fires.
   `_plot_dirty` is `True`, so it clears the flag and calls `_redraw_waveform`
   — which reads `self._last_state`, now the *idle* snapshot, hits

   ```python
   if state is None or not state.corrected_downsampled:
       return
   ```

   and returns having drawn nothing. The pending redraw is consumed and never
   comes back.
5. `_redraw_fwhm_history` runs next and **does** draw, because it does not read
   `_last_state` at all — it reads `self._fwhm_history`, which step 1 appended
   to inside `_on_scope_state`.

That is the whole symptom: history updates, waveform never does. The race is
~1000 ms wide against a ~10 ms window, so it loses essentially every time.

The same overwrite silently breaks the readout. `_redraw_readout` runs on a
200 ms timer and starts with:

```python
if state.idle:
    self._lbl_status.setText("idle — scope connected, press Take shot (F5)")
    return
```

so the X/Y width labels, r², S/N and transfer cost are usually never written
either — they only look right because a *previous* good draw is still on
screen, or because the readout timer occasionally wins the race.

### The decisive test before you change anything

Switch the tab to **continuous** mode and take shots. In continuous mode the
loop sleeps at the bottom and sets `was_idle = False` again, so **no idle
snapshot is ever emitted between acquisitions** — and the waveform will draw
normally. Single shot blank + continuous fine confirms this diagnosis. Do this
first; if continuous is *also* blank, stop and re-diagnose rather than
applying the fix below on faith.

### Do NOT fix it in the worker

The obvious-looking fix — stop emitting the idle snapshot, or set
`idle=False`, or delay it — is wrong. That snapshot is load-bearing: it drives
the connection pill (`● Connected — idle`), the "press Take shot (F5)" status
line, and the operator's only signal that the link is up but not transferring.
The bug is that **the GUI uses one variable for two different things**. Fix it
there.

### The fix — `rbl/gui/profiler_tab.py`

Split the cache in two: the latest snapshot of *any* kind, which is about
connection status, and the latest snapshot that actually *carries a
measurement*, which is what every number and every plot is drawn from.

**1. Add the second cache field** in `__init__`, next to `self._last_state`:

```python
self._last_state = None      # latest snapshot of ANY kind — status only
self._last_measured = None   # latest snapshot that CARRIES a measurement
```

Comment it with why, in the house style: an idle or error snapshot is a
statement about the link, not about the beam, and must never be allowed to
erase the last thing measured.

**2. Add a module-level predicate** so "carries a measurement" is defined once
and both timers agree on it:

```python
def _carries_measurement(state) -> bool:
    """True when this snapshot holds beam data, not just link status.

    An idle ping and an error report are both `connected` snapshots with
    every data field at its default. They say something about the link; they
    say nothing about the beam, and treating them as data is what blanked
    the waveform pane after every shot.
    """
    return bool(state.corrected_downsampled) or bool(state.peaks)
```

**3. In `_on_scope_state`**, keep the status assignment, gate the rest:

```python
self._last_state = state
self._last_time  = time.time()
self._readout_dirty = True

if _carries_measurement(state):
    self._last_measured = state
    self._plot_dirty    = True

if self._shot_pending and not state.idle:
    self._shot_finished()
```

Note `_plot_dirty` now keys off the measurement, not off `not state.idle` —
an error snapshot has `idle=False` and no trace, and used to arm a redraw
that could only early-return.

**4. Also in `_on_scope_state`, stop padding the history with empty rows.**
Today every snapshot — idle pings, disconnects, error reports — appends a
`(t, nan, [])` row. Wrap the append in the same predicate:

```python
if _carries_measurement(state):
    per_peak = [(p.get("axis", "?"), p.get("fwhm_seconds", math.nan))
                for p in state.peaks]
    self._fwhm_history.append((time.time(), state.fwhm_seconds, per_peak))
    if len(self._fwhm_history) > _MAX_HISTORY:
        self._fwhm_history = self._fwhm_history[-_MAX_HISTORY:]
```

**5. `_redraw_waveform` draws from `self._last_measured`**, not
`self._last_state`, and reports whether it drew:

```python
def _redraw_waveform(self) -> bool:
    state = self._last_measured
    if state is None or not state.corrected_downsampled:
        return False
    ...
    self._canvas_wave.draw_idle()
    return True
```

Everything else in that method is unchanged — it was correct, it was just
being handed the wrong object.

**6. `_redraw_plots` must not consume a pending redraw it did not perform:**

```python
def _redraw_plots(self):
    if not self._plot_dirty:
        return
    # Clear only on a draw that happened. Clearing first and then returning
    # early is how a redraw armed by a good snapshot got thrown away.
    if self._redraw_waveform():
        self._plot_dirty = False
    elif self._last_measured is None:
        self._plot_dirty = False      # nothing to draw yet; don't spin
    self._redraw_fwhm_history()
```

**7. `_redraw_readout`: status from `_last_state`, numbers from
`_last_measured`.** Restructure the top of the method so the idle branch sets
the status line and the pill but no longer returns before the numbers are
written:

```python
def _redraw_readout(self):
    if not self._readout_dirty:
        return
    self._readout_dirty = False

    status = self._last_state
    if status is None:
        return

    # ---- link status: from the latest snapshot of any kind ---------------
    if status.connected and status.idle:
        self._pill.set_connected(True, "● Connected — idle")
    elif status.connected and status.continuous:
        self._pill.set_connected(True, "● Connected — continuous")
    else:
        self._pill.set_connected(bool(status.connected))
    self._lbl_channel.setText(status.channel)

    if status.idle:
        self._lbl_status.setText("idle — scope connected, press Take shot (F5)")
        self._lbl_status.setStyleSheet(
            f"color: {theme.NEUTRAL}; font-style: italic;")

    # ---- the numbers: from the last snapshot that measured something -----
    state = self._last_measured
    if state is None:
        return
    ... # body unchanged from here, but every `state.` now reads _last_measured
```

Keep the existing status-line block at the bottom (`if state.error: … elif
state.connected: "OK"`) but guard it so it does not stomp the idle line you
just set — an idle snapshot should read "idle", not "OK", while the widths
above it still show the last shot. The rule to encode: **the status line
describes the link and comes from `status`; every value above it describes
the beam and comes from `state`.**

### Tests to add — `tests/test_scope_acquisition_and_ports.py`

Follow the existing file's style (the `qapp` fixture with
`QT_QPA_PLATFORM=offscreen` is already there). You will need a stub beamline —
a `QObject` exposing `scope_changed = Signal(object)`, `scope_error =
Signal(str)` and no-op `request_scope_shot` / `set_scope_*` methods. Check
what `ProfilerTab.__init__` actually calls on `beamline` and stub exactly
that; if the widget turns out to be too heavy to instantiate offscreen, pull
the cache logic into a small pure helper and test that instead rather than
skipping the test.

Pin these:

- **`test_an_idle_snapshot_does_not_erase_the_last_trace`** — feed a full
  `ScopeState` then an `idle=True` one; assert `_last_measured` still holds
  the trace and `_redraw_waveform()` returns `True`.
- **`test_a_shot_followed_by_idle_still_draws_the_waveform`** — the exact
  worker sequence: full state, idle state, then call `_redraw_plots()` once
  and assert the waveform axes has lines on it
  (`len(tab._ax_wave.lines) > 0`). This is the regression; name it so it is
  obvious what broke.
- **`test_an_error_snapshot_does_not_arm_an_empty_redraw`** — a state with
  `error=…` and no trace leaves `_plot_dirty` False.
- **`test_idle_snapshots_do_not_pad_the_fwhm_history`** — ten idle snapshots
  add zero history rows.

---

## Bug 2 — `identify()` logs at INFO and the idle keepalive calls it every 5 s

### What is happening

`ScopeWorker._run_loop`'s idle branch pings the scope so a pulled cable is
noticed while nothing is being transferred:

```python
_KEEPALIVE_S = 5.0
...
if time.monotonic() >= keepalive_due:
    keepalive_due = time.monotonic() + _KEEPALIVE_S
    driver.reset_buffers()
    driver.identify()
```

and `Tds2012.identify()` (`rbl/hardware/tds2012_driver.py`, ~line 400) ends
with:

```python
log.info("tds2012: identified — %s", ident)
```

`rbl/gui/app.py:main()` calls `logging.basicConfig(level=logging.INFO)`, so
that line reaches the terminal. In single-shot mode the worker is idle
essentially all the time, so it prints every 5 s forever. The line is also
redundant: `ScopeWorker._connect()` already logs the same identity string at
INFO when the link opens.

### The fix

1. **Demote the per-call line.** In `Tds2012.identify()`, change `log.info` to
   `log.debug`. Nothing is lost — the connect path already reports the
   identity at INFO, once, where it is actually news.

2. **Log the keepalive only when the answer changes.** In `ScopeWorker`, record
   the identity string returned by `_connect()`, and in the keepalive compare:
   if the string differs from the one recorded at connect, log **INFO** ("the
   instrument on this port changed") — that is a real event worth a line —
   and otherwise say nothing. Store it on the worker (e.g. `self._ident`) and
   have `_connect()` return it or set it.

3. **Slow the keepalive down.** Raise `_KEEPALIVE_S` from `5.0` to `15.0`.
   Justify it in the comment: this is a cable-pull detector for a link that is
   deliberately doing nothing. Fifteen seconds' notice costs nothing, because
   the next shot reports a dead link immediately anyway, and it cuts the idle
   serial traffic by two thirds.

Do **not** delete the keepalive. Noticing a pulled cable while idle is the
reason it exists; the defect is its log level and its rate, not the ping.

### Test

- **`test_identify_does_not_log_at_info`** — call `Tds2012.identify()` against a
  fake transport under `caplog.at_level(logging.INFO)` and assert no record
  from `rbl.hardware.tds2012_driver` was emitted. There are existing fake
  transports in the scope tests; reuse one rather than writing another.
- Assert `_KEEPALIVE_S >= 15.0` so the interval is not quietly walked back.

---

## Order of work

1. Run the continuous-mode check described under Bug 1 and confirm the
   diagnosis on the bench before editing.
2. Fix Bug 1 in `rbl/gui/profiler_tab.py` only. No worker changes.
3. Fix Bug 2 across `tds2012_driver.py` and `scope_worker.py`.
4. Add the tests above; run the whole suite, not just the new file — 
   `test_profile_multipeak.py` and `test_profile_fwhm.py` pin behaviour the
   readout depends on.
5. Verify on hardware: one shot draws one waveform, the idle line still says
   "idle — press Take shot (F5)", the widths stay on screen between shots, and
   the terminal is quiet while idle.

## What must still be true afterwards

- The worker still idles by default; connecting still transfers nothing.
- The idle snapshot is still emitted, and the pill still shows
  `● Connected — idle`.
- A measurement stays on screen between shots instead of being blanked by the
  next status ping.
- A cable pulled while idle is still noticed, within 15 s.
