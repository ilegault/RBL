# 72: `MainWindow` constructs the `CaptureClient`, feeds it the cup position, and shuts it down

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 71

**Spec:** `.scratch/zwo-capture/spec.md`, section D9 (last three paragraphs)
**Binding:** `docs/adr/0008-zwo-camera-owned-by-a-capture-process.md` (decision 5),
`docs/adr/0003-commanded-and-confirmed-cup-position.md`,
`docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/gui/app.py` — `MainWindow.__init__` around the `SessionRecorder(`
construction and the `cup_actuation_changed.connect(...)` block (copy its comment style: every
connection says why it exists), and `MainWindow.closeEvent`. `AGENTS.md` §3 ("`MainWindow.__init__`
is the wiring diagram").

## What to build

In `src/rbl/gui/app.py` only:

1. In `MainWindow.__init__`, construct `self.capture_client = CaptureClient(self)` immediately
   before `self.session_recorder = SessionRecorder(...)`, and pass `capture=self.capture_client`
   to it. One comment line: the client is the only owner of the Capture Process (ADR 0008),
   constructed but not started; the child launches only when the operator connects the ZWO
   camera.
2. Next to the existing `self.beamline.cup_actuation_changed.connect(...)` lines, add
   `self.beamline.cup_actuation_changed.connect(self.capture_client.on_cup_actuation_state)`
   with a comment: the frame log records the end-station cup's confirmed position (ADR 0003),
   the client sends it to the Capture Process only when the label changes.
3. In `closeEvent`, right after the `session_recorder.stop` line, add
   `best_effort("capture_client.shutdown", self.capture_client.shutdown)`. Order matters and the
   comment says why: the session's stop sends `stop_recording` first, so the last segment is
   closed by the reason `"stop"` rather than `"shutdown"`.

No new behaviour at start-up: constructing the window must not launch a process.

## Acceptance criteria

`tests/test_capture_wiring.py` (new). No `_`-prefixed attribute access.

- [x] `test_window_has_one_client_and_launches_nothing`: `MainWindow()`;
  `win.capture_client` is a `CaptureClient`; `win.capture_client.is_running()` is False after
  `processEvents`; `win.session_recorder.camera_kind() == "usb"` (fresh store from conftest).
- [x] `test_cup_state_reaches_the_client`: `MainWindow()`; on `win.capture_client`:
  `start("fake")`, `open()`, `apply(roi={"x": 0, "y": 0, "width": 64, "height": 32})`,
  `start_recording` into `tmp_path` directly; then
  `win.beamline.cup_actuation_changed.emit(CupActuationState(connected=True,
  confirmed=CupPosition.IN))`; within 10 s `tmp_path/zwo/frames.csv` has a row whose
  `cup_position` is `IN`. (This proves the connection in `__init__`, which is the thing under
  test; nothing is monkeypatched.)
- [x] `test_close_shuts_the_child_down`: `MainWindow()`, `win.capture_client.start("fake")`,
  wait for `hello`; `win.close()`; within `CAPTURE_SHUTDOWN_WAIT_S + 2` s the client emitted
  `exited(0)` and `is_running()` is False.
- [x] (no test: regression, proved by the existing suite in the gate) Existing `MainWindow` tests pass unchanged.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile

Soft type-gate count must not rise above `tools/mypy_ratchet.txt`.

## Out of scope

- Any widget change (73-75).
- Starting the Capture Process automatically at application start.

## Comments

### Landed 2026-10-29
- Constructed `CaptureClient(self)` in `MainWindow.__init__` and passed `capture=self.capture_client` to `SessionRecorder`.
- Wired `self.beamline.cup_actuation_changed.connect(self.capture_client.on_cup_actuation_state)`.
- Added `best_effort("capture_client.shutdown", self.capture_client.shutdown)` in `MainWindow.closeEvent` right after session stop.
- Covered by `tests/test_capture_wiring.py` (`test_window_has_one_client_and_launches_nothing`, `test_cup_state_reaches_the_client`, `test_close_shuts_the_child_down`).
- Existing `MainWindow` tests pass unchanged without regressions.
