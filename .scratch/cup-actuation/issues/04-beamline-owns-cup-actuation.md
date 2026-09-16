# 04: Beamline owns cup actuation

**What to build:** The application can command the cup out and back, and knows where the
cup actually is — through `Beamline`, like every other instrument.

After this ticket a test can inject raw `FIO_STATE` integers into a real `Beamline` wired
as `MainWindow` wires it, command a move, and watch a typed snapshot come out carrying
commanded position, confirmed position, AUTO state and staleness. Nothing is on screen
yet; that is ticket 05.

**Blocked by:** 02, 03

**Status:** ready-for-agent

**Read `docs/adr/0003-commanded-and-confirmed-cup-position.md` first.**
`docs/adr/0001-tests-first-and-no-muted-failures.md` is binding.

## Structural requirements

- **One instrument, one owner.** A new `CupActuationLinkMixin` in `rbl/state/`, shaped
  like `vacuum_link.py`, mixed into `Beamline`. No widget constructs, holds, or tears
  down anything, and no widget converts a status word into a position.
- **One LJM handle, one thread.** `labjack_stream_worker.py` owns the handle for its
  lifetime and all `eStream*` calls happen on its thread. Writing a digital output
  during a stream is permitted on the T7 — the documented restriction is on
  command-response *analog input* reads, which this feature never needs — but two
  threads must not use one handle. So the worker gains a **thread-safe pending-write
  slot drained between `eStreamRead` calls**, and publishes the resulting output state
  as a signal. No other thread touches the handle. Writes happen a handful of times per
  cycle period, so the cost to the read loop is negligible.
- The snapshot is a **frozen dataclass in `rbl/state/snapshots.py` carrying
  `connected`**, like every other snapshot, so a view can tell "not hooked up" from "no
  data yet".
- **Relay 1 (enable) is held closed while the application is connected and in control,
  and opened on disconnect and on application shutdown**, as an explicit release of
  control. Relay 2 (command OUT) is the one that cycles.
- **Nothing drives the cup as a safety measure.** Shutdown opens both relays, which is
  a release of drive, not a command to a position. Do not add a handler that drives the
  cup anywhere on crash, exception, or exit.

## The snapshot

Carries at minimum: `connected`; commanded position; confirmed position (from the
ticket 02 decode, applied to the streamed word); whether the controller is in AUTO;
whether the status reading is stale; and the timestamp of the last confirmed transition.

Staleness means the `FIO_STATE` entry was absent or `None` in the last window — which is
what happens in every profile but `FULL` — or no window has arrived within a stale
threshold. It is not the same thing as disconnected and must be distinguishable from it.

- [ ] `CupActuationLinkMixin` exists in `rbl/state/`, is mixed into `Beamline`, and owns
      the only path that commands a cup move
- [ ] A frozen `connected`-carrying snapshot is published as a `Beamline` signal on every
      window and on every commanded change
- [ ] Commanded position and confirmed position are separate fields and are never
      collapsed into one
- [ ] The stream worker drains a thread-safe pending-write slot between `eStreamRead`
      calls and emits the resulting output state; no code outside the worker thread
      touches the LJM handle
- [ ] Contact debounce from `cup_config.py` is applied to confirmed transitions, using
      the per-scan transition times from ticket 03's payload entry, not window edges
- [ ] The last-confirmed-transition timestamp is derived from the window `t` and
      `sample_period`, so it resolves to one sample rather than one window
- [ ] Relay 1 closes on connect and opens on `Beamline.shutdown()` and on LabJack
      disconnect; relay 2 is de-asserted at the same points
- [ ] `Beamline.shutdown()`'s existing teardown order is preserved and its docstring
      updated; the cup release happens before the T7 handle is closed
- [ ] Staleness is set when `FIO_STATE` is `None` in the window, and is distinguishable
      in the snapshot from `connected=False`
- [ ] `tests/payloads.py` gains a cup-actuation feed — following `LabJackFeed`'s shape
      and reading `CupFeed`'s docstring first — that injects raw `FIO_STATE` integers
      into a real `Beamline` and exposes the commanded-move entry point
- [ ] Tests, through that feed: a commanded OUT followed by a confirming status word
      produces a snapshot whose commanded and confirmed both read OUT; a commanded OUT
      with no confirming word leaves confirmed unchanged; a word arriving in a non-FULL
      profile marks the snapshot stale without marking it disconnected; both contacts
      asserted reaches the snapshot as indeterminate
- [ ] A test asserts `Beamline.shutdown()` leaves both output lines de-asserted and
      issues no positional cup command
- [ ] `scripts/check_layers.py` reports no new upward import
- [ ] `ruff check .`, `python scripts/check_tests_first.py`, `python tools/type_gate.py`
      and `pytest --tb=short -q -n auto --dist loadfile` all pass
