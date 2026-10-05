# Spec: Daily vacuum monitoring log, sessions that own their files, and a cup log the operator opens

Status: ready-for-agent
Date: 2026-10-05
Related: `docs/adr/0004-monitoring-log-and-sessions.md` (new, binding),
`docs/adr/0002-cup-acquisition-triggered-by-current.md` (read the **amendment dated
2026-10-05**, decisions B1-B5), `docs/adr/0003-commanded-and-confirmed-cup-position.md`
(read the **amendment dated 2026-10-05**, decisions C1-C5),
`docs/adr/0001-tests-first-and-no-muted-failures.md`,
`CONTEXT.md` ("Logging and sessions", and "Known collisions": session, arm),
`.scratch/cup-settings/spec.md` (the ticket set this one must land after, for Part C).

> ADR 0001 is binding on all test work here: a failing test is fixed or escalated,
> never muted.

## Problem Statement

**The vacuum log never closes.** `VacuumTab._on_vacuum_state` starts a `VacuumLogger`
on the first reading after launch and writes one row per 1 Hz poll
(`vacuum_config.POLL_INTERVAL_S = 1.0`) to one file until the application exits. Two
weeks produced a 176 MB CSV that cannot be opened. `VacuumLogger` only ever closes a
file on Stop or when the gauge set changes.

**Stop Logging does not stop.** `VacuumTab._stop_logging` sets `self._logger = None`, and
the next `_on_vacuum_state` sees `None` and calls `_start_logging()` again, within one
second.

**The vacuum logger's docstring is wrong three ways.** It says a log opens on "start
logging" (it starts on its own), that files are named `vacuum_YYYYMMDD_HHMMSS` (the code
writes `vacuum_YYYYMMDDTHHMMSS`), and that they live under `dist/RBL/data/vacuum/` (they
live under `config/paths.VACUUM_DIR`, `~/Desktop/RBL_log/data/vacuum/`).

**A session has no pressure record of its own.** The session's `data.csv` carries
pressure every 5 s with no gauge state. Nothing writes the 1 Hz vacuum record into a
session folder.

**Video cannot start partway through a session, and a second start would destroy the
first.** `SessionRecorder.set_video_enabled` returns early while recording, so video is
fixed at session start. Were that guard simply removed, `VideoRecorder.start` would open
`frames.csv` with mode `"w"` and its `_segment_index` restarts at `0`, so the second video
run would overwrite `video_000` and truncate the frame index.

**The cup writes all the time, and counts everything.** `FaradayCupTab.__init__`
constructs `CupSessionWriter()` when the tab is built, so a cup file is open for the whole
life of the application, with a heartbeat every `CUP_IDLE_HEARTBEAT_INTERVAL_S = 10.0`.
Automatic insertion can run with nothing deliberately opened to record it. And every
`RunClosed` in `on_cup_state` goes into `self._accumulator.record_insertion`, so the hand
insertions made before an irradiation to check the beam (alumina still in front of the
specimen) are credited to the dose.

**The words on screen are opaque.** "Arm Cycle", "arm threshold" and "release threshold"
mean nothing to someone new at the bench, and "arm" names two unrelated things.

## Solution

Three parts. Part A and Part B have no dependency on any open ticket set. Part C edits
`faraday_cup_tab.py` and `cup_session_writer.py`, which `.scratch/cup-settings/` tickets
05-08 and 12-16 also edit, so **every Part C ticket is blocked by cup-settings ticket 16**.

**Part A: the vacuum monitoring log.** A pure helper decides file boundaries and names. The
monitoring log rolls at local midnight into month folders, Stop Logging really stops, and
a fresh launch starts it.

**Part B: sessions own their files.** A session writes its own 1 Hz `vacuum.csv`. Video
gets Start Video and Stop Video buttons that work any number of times inside a session
without overwriting anything.

**Part C: the cup log.** The cup writes only while a cup log is open, opened by a session
or from a new Overview cup panel. Automatic cup insertion requires one. Only automatic
insertions count toward the dose. Stopping asks for confirmation, restarting asks whether
the beam was on, and a new session offers to continue the previous session's dose. The
on-screen words change.

## User Stories

1. As the operator, I can open yesterday's vacuum record in Excel, because it is one day.
2. As the operator, I find a night's pressure by opening the month folder and the file
   with that date.
3. As the operator, I press Stop Logging on the Vacuum tab and it stays stopped until I
   press Start Logging or relaunch.
4. As the operator, I launch the application and the monitoring log is already running.
5. As the operator, every session folder holds the experiment's full 1 Hz vacuum record,
   whether or not the monitoring log was running.
6. As the operator, I start a session without video, start video when something
   interesting happens, stop it, and start it again later, and every clip survives.
7. As the operator, nothing is written for the cup unless I started a session or a cup
   test log, and the screen says "Not logging" when nothing is.
8. As the operator, I can test the cup without a session, and see where that test file is
   going the way the Vacuum tab shows its path.
9. As the operator, the application never inserts the cup on its own schedule while
   nothing is recording it.
10. As the operator, my hand insertions to check the beam are recorded but never added to
    the dose.
11. As the operator, stopping automatic insertion warns me what it means for the dose
    record and then lets me do it.
12. As the operator, restarting automatic insertion asks if I want to pick up where I left
    off, and whether the beam was on in the meantime, and the file says what I answered.
13. As the operator, a new session asks whether to continue the previous session's dose,
    so an irradiation over a weekend stays one dose record.
14. As someone new at the bench, the buttons say what they do: "Start automatic cup
    insertion", "Run start current".

## Implementation Decisions

### A1. The rollover helper is pure

New module `src/rbl/services/log_rollover.py`. No Qt, no clock, no file writes. Every
function takes timezone-aware local `datetime` values from its caller; callers obtain them
with `datetime.now().astimezone()`. It is pure so that midnight, month-end, New Year and
the daylight-saving changes can be tested by passing times, not by waiting for them.

```python
def month_folder(root: Path, local_dt: datetime) -> Path        # root / "YYYY-MM"
def timestamped_stem(prefix: str, local_dt: datetime) -> str    # f"{prefix}_{YYYYMMDDTHHMMSS}"
def crossed_local_midnight(opened_local: datetime, now_local: datetime) -> bool
                                                                # now_local.date() != opened_local.date()
def unused_path(folder: Path, stem: str, suffix: str) -> Path   # folder/stem+suffix, else stem_2, stem_3, ...
```

`unused_path` is the only function that looks at the filesystem (existence check only). It
is what guarantees no log file is ever overwritten.

### A2. `VacuumLogger` gains a file stem and stops overwriting

`src/rbl/services/vacuum_logger.py`, `VacuumLogger.__init__` gains a keyword
`file_stem: str | None = None`. When given, the CSV and sidecar are named from it, and in
both cases the paths come from `log_rollover.unused_path(output_dir, stem, ".csv")` with
the sidecar sharing the chosen stem. The row format, the `#` header and the sidecar
contents do not change. The module docstring is corrected: auto-start, the real name
pattern, the real folder, month folders and midnight rollover, with a `WHY THIS EXISTS`
note recording the 176 MB file.

### A3. `VacuumMonitorLog` owns the rollover rule

New class in new module `src/rbl/services/vacuum_monitor_log.py`. No Qt.

```python
class VacuumMonitorLog:
    def __init__(self, root: Path | None = None, *, comment_lines_fn=None) -> None
    def write(self, state, now_local: datetime) -> None
    def start(self) -> None
    def stop(self) -> str | None          # closes the open file, returns its path
    @property
    def is_running(self) -> bool          # True on construction
    @property
    def current_path(self) -> str | None
```

`root` defaults to `config.paths.VACUUM_DIR`. `comment_lines_fn(state) -> list[str]`
builds the `#` header; the tab passes its existing `_build_comment_lines`.

`write` rules, in order:

1. Not running: return, write nothing.
2. No file open: open one at `month_folder(root, now_local)` with stem
   `timestamped_stem("vacuum", now_local)`, write the header, then the row.
3. `crossed_local_midnight(opened_at, now_local)`: close the open file, open the next in
   the month folder of `now_local`, write the header, then this row. This row is the new
   file's first row. It is not written to the old file.
4. Gauge set changed (`VacuumLogger.write_row` returns `False`): close, open a new file
   for the new gauge set at the same `now_local`, header, row.

`stop()` closes and sets not-running. `start()` sets running; the next `write` opens a
file. Midnight never calls `start()`.

### A4. The Vacuum tab uses `VacuumMonitorLog`

`src/rbl/gui/vacuum_tab.py`: `self._logger` becomes `self._monitor_log = VacuumMonitorLog(
comment_lines_fn=self._build_comment_lines)`. `_on_vacuum_state` calls
`self._monitor_log.write(state, datetime.now().astimezone())` and nothing else about
logging. The auto-start branch and the inline reopen block are deleted.
`_on_log_toggle`, `_start_logging`, `_stop_logging` call `start()`/`stop()`. The path label
shows `current_path` while running and `Saved: <path>` / `Stopped` after a stop. It
refreshes on every write, so after midnight it shows the new file without a click.

### B1. The session writes its own `vacuum.csv`

`src/rbl/services/session_recorder.py`, `SessionRecorder` gains a public slot
`on_vacuum_state(state) -> None`. While recording, it writes to a `VacuumLogger(labels,
output_dir=<session folder>, file_stem="vacuum")`, opened lazily on the first state after
`start()`, with header lines built by the same rule as the monitoring log. The gauge-change
rule from A3.4 applies (the second file becomes `vacuum_2.csv` via `unused_path`). Midnight
does nothing here. `stop()` closes it. `session.json` gains `"vacuum": {"files": [...]}`
listing every vacuum file of the session. This slot never touches the monitoring log.

`src/rbl/gui/app.py`, `MainWindow.__init__`: connect `self.beamline.vacuum_changed` to
`self.session_recorder.on_vacuum_state`, with a comment giving the reason (ADR 0004
decision 6).

The shared header-line rule moves out of `VacuumTab._build_comment_lines` into a pure
function `vacuum_comment_lines(state, utc_now: datetime) -> list[str]` in
`vacuum_logger.py`, used by both.

### B2. Video starts and stops inside a session

`SessionRecorder`:

- `set_video_enabled` and `_video_enabled` are removed. Video never starts with
  `start()`.
- New `start_video() -> bool`. Refuses (returns `False`, emits `status` with the reason)
  when not recording, when the camera is not open, or when video is already running.
  Otherwise builds the `VideoRecorder` as `_start_video` does today.
- New `stop_video() -> None`. Stops the recorder and disconnects `frame_ready`. Transcode
  handling is unchanged.
- `stop()` calls `stop_video()` first.
- `events.csv` gets `video_started` and `video_stopped` rows. `session.json` `video` gains
  `"runs": [{"started_t_rel": float, "stopped_t_rel": float | None,
  "first_segment": int, "last_segment": int | None}]`. `"enabled"` is true when any run
  exists.
- The camera closing while video runs ends that video run with a `video_stopped` event
  whose detail is `camera_closed`. The session keeps running.

`src/rbl/services/video_recorder.py`, `VideoRecorder`:

- `__init__` gains `first_segment_index: int = 0`; `_segment_index` starts there.
  `SessionRecorder` passes the next unused index, so names continue `video_003`,
  `video_004`, ... across runs.
- `start()` opens `frames.csv` in append mode, and writes the header row only when the file
  is new or empty.

These two changes are what stop a second video run from overwriting the first. They are
requirements, not preferences.

### B3. The video controls

`src/rbl/gui/widgets/recording_panel.py` and `src/rbl/gui/camera_tab.py` (both bind the
one recorder): the `Record video` checkbox becomes a button reading `Start Video` /
`Stop Video`. It is enabled only while a session is recording and the camera is open, and
its tooltip says which of the two is missing when disabled. Frame rate, segment length,
quality and codec stay editable only while video is not running. The session button text
stays `● START SESSION` / `■ STOP SESSION`.

### C1. One owner for the cup log

New module `src/rbl/services/cup_log.py`.

```python
class CupLogKind(Enum): SESSION = "session"; TEST = "test"

class CupLog(QObject):
    opened = Signal(str, str)        # path, kind value
    closed = Signal(str)             # path
    def open_for_session(self, folder: Path) -> CupSessionWriter
    def open_test(self, now_local: datetime) -> CupSessionWriter
    def close(self) -> str | None
    @property writer -> CupSessionWriter | None
    @property kind -> CupLogKind | None
    @property path -> str | None

def read_dose_totals(csv_path: Path) -> DoseTotals | None     # pure, no Qt
def find_previous_session_cup_log(logs_dir: Path, exclude: Path) -> Path | None
```

- `DoseTotals` (frozen dataclass: `total_charge_c`, `total_beam_on_s`,
  `insertion_count`, `last_out_t`, `last_current_a`, `source_path`) lives in
  `rbl/hardware/dose_model.py`, not here, because `DoseAccumulator.seed` takes it and
  `hardware` must not import from `services`.
- A session cup log is `CupSessionWriter(session_id="cup", output_dir=<session folder>)`
  (`cup.csv`, `cup.json`). A test cup log uses
  `month_folder(FARADAY_CUP_DIR, now_local)` and `timestamped_stem("cup", now_local)`,
  passed through `unused_path`.
- Opening while one is open closes the open one first (emitting `closed`). There is never
  more than one.
- `read_dose_totals` reads the last insertion-summary row whose `counted_in_dose` is
  `true` (C4) and returns `None` when there is none. It is pure so it can be tested
  against files a real `CupSessionWriter` wrote.
- `find_previous_session_cup_log` returns the newest `session_*/cup.csv` under `logs_dir`,
  excluding `exclude`, for which `read_dose_totals` is not `None`.

`MainWindow` constructs one `CupLog` and passes it to `FaradayCupTab` and to the new
Overview panel. `CupSessionWriter` is no longer constructed in `FaradayCupTab.__init__`.

### C2. The Faraday Cup tab writes only through an open cup log

`src/rbl/gui/faraday_cup_tab.py`:

- The constructor takes `cup_log: CupLog | None = None` in place of `session_writer`. When
  `None` it builds its own, so existing tests that build the tab bare keep working.
- Every `self.session_writer.<write>` call goes through the current writer and is skipped
  when no cup log is open. No write reaches a closed writer.
- `CupLog.opened` resets the dose accumulator to zero, or seeds it (C6).
  `CupLog.closed` stops automatic cup insertion as an operator stop does (withdraw, keep
  the saved boundary), without the C5 dialog, because the operator already confirmed
  closing.
- With no cup log open, the tab shows `Not logging` in `theme.WARN` beside the current
  readout, and the automatic insertion start button is disabled with the visible reason
  `Start a session or a cup test log to use automatic cup insertion.` This check comes
  first, before the dose-chain preconditions in `_check_arm_preconditions`.
- Manual Insert/Retract stays enabled regardless of the cup log.

### C3. The Overview cup panel

New `src/rbl/gui/widgets/cup_panel.py`, `CupPanel(QWidget)`, added to the Overview panel
area the way `RecordingPanel` is. It shows the live cup current, confirmed position
(IN / OUT / in transit), run state (run open or not), automatic insertion state (running
or stopped), and accumulated charge, fluence and dpa. It has `Start Logging` /
`Stop Logging` for a test cup log, and a path line styled like the Vacuum tab's: the file
path in `theme.OK` while logging, `Not logging` in `theme.WARN` when not. While a session
owns the cup log the button is disabled with the tooltip
`The session owns the cup log. Stop the session to end it.`

It renders a snapshot, it does not compute. `FaradayCupTab` gains a signal
`cup_view_changed(object)` emitting a frozen dataclass `CupView` (defined in `cup_log.py`:
`logging: bool, log_path: str | None, run_open: bool, automatic_running: bool,
charge_c: float, fluence: float | None, dpa: float | None`). It is emitted from
`_update_dose_view`, on cup log open and close, and on automatic start and stop.
`MainWindow` connects it to the panel. One accumulator, one place the numbers come from
(AGENTS.md invariant 2).

### C4. Only automatic insertions count

`src/rbl/hardware/dose_model.py`, `DoseAccumulator` (pure, no Qt):

- New `exclude_interval(start_t: float, end_t: float) -> None`. The excluded span is
  subtracted from whichever hold interval `[last_out_t, t_in]` it overlaps, at the next
  `record_insertion`. Overlapping exclusions are merged, not double-subtracted. An
  exclusion outside the next hold interval has no effect.
- New `seed(totals: DoseTotals) -> None`, setting charge, beam-on, insertion count,
  `last_out_t` and `last_current_a`, so the next insertion's hold interval runs from the
  previous log's last withdrawal.
- `record_insertion`'s formula is unchanged apart from the exclusion.

`FaradayCupTab`:

- An insertion is **automatic** when the cup's confirmed IN followed a `CycleInsert`
  command from the scheduler. The tab sets this when it acts on `CycleInsert` in
  `_tick_cycle` and clears it on the confirmed OUT. Everything else is manual.
- On `RunClosed`: automatic, `record_insertion` as now. Manual: no `record_insertion`; the
  span from confirmed IN to confirmed OUT is passed to `exclude_interval` (the run's open
  and close times when position feedback is unavailable).
- `write_insertion_summary` gains `origin: str` (`"automatic"`, `"manual"`, `"forced"`,
  `"uncommanded"`), `counted_in_dose: bool`, and `total_beam_on_s: float`.
  `cup_session_writer.CSV_COLUMNS` gains `origin`, `counted_in_dose`, `total_beam_on_s`,
  appended after the existing columns. A manual summary row carries the running totals
  unchanged.

### C5. Stopping and restarting automatic cup insertion

`FaradayCupTab`:

- The arm/disarm toggle on one button is gone. There are two buttons, `Start automatic cup
  insertion` and `Stop automatic cup insertion`. Stop always withdraws the cup if it is in
  (today's `_on_cycle_stop_clicked`). The disarm-in-place path is removed.
- Operator Stop calls `self._confirm_stop_automatic() -> bool` first, a method wrapping a
  `QMessageBox`, so tests can replace it. Text:
  `Stopping automatic cup insertion withdraws the cup and pauses the dose record. No
  insertions happen until you start it again. When you do, you will be asked whether the
  beam was on the specimen during the pause.`
  Buttons: `Stop automatic insertion` / `Keep running`. Stops forced by a fault (ADR 0003
  decision 3) do not ask.
- The stop time is kept as the stop-gap start, along with the saved boundary.
- Start, when this cup log has a stop gap, calls
  `self._ask_restart(gap_start_t, now_t, saved_boundary_t) -> RestartChoice | None`,
  also a replaceable method. `RestartChoice` is a frozen dataclass in `cup_log.py`:
  `resume_schedule: bool, beam_on_during_gap: bool`. The dialog asks
  `Was the beam on the specimen while automatic cup insertion was stopped
  (<gap start> to <now>)?` with no default (Yes / No must be chosen), and offers
  `Pick up where you left off` (resume the saved boundary, enabled only while it is
  still in the future) or `Start a new schedule now`. Cancel returns `None` and nothing
  starts.
- `beam_on_during_gap=False` calls `exclude_interval(gap_start_t, now_t)`. A new writer
  method `write_automatic_insertion_restarted(t_host, gap_start_t, gap_end_t,
  beam_on_during_gap, mode)` records it, `mode` being `"resumed"` or `"fresh"` as returned
  by the scheduler.
- This replaces cup-settings ticket 16's `chk_keep_schedule` checkbox. The scheduler's
  `resume_at` argument and the cycle armed/disarmed rows from that ticket stay.
- The first start in a cup log has no gap and asks nothing.

### C6. Sessions open the cup log and offer dose continuation

`SessionRecorder` gains `set_start_guard(fn)` and `set_stop_guard(fn)`
(`Callable[[], bool]`), checked at the top of `start()` and `stop()`; `False` means do
nothing. The disk auto-stop calls `stop(force=True)`, which skips the guard.
`MainWindow` installs a start guard that, when automatic cup insertion runs in a test cup
log, runs the tab's confirmed stop and returns whether it stopped.

`MainWindow`, on `session_recorder.session_started(folder)`:

1. (Handled by the start guard above.)
2. `previous = find_previous_session_cup_log(LOGS_DIR, exclude=folder)`. If found,
   ask through a replaceable method on `MainWindow`, `_ask_continue_dose(totals) ->
   ContinueChoice | None`, with
   `Continue the dose from <session id> (charge <Q> C, <dpa> dpa, last insertion
   <time>)?` and the same beam-on question for the gap from `totals.last_out_t` to now.
   `ContinueChoice(continue_dose: bool, beam_on_during_gap: bool)`.
3. `cup_log.open_for_session(folder)`. When continuing, the tab seeds the accumulator
   from the totals, applies `exclude_interval` for a no-beam gap, and the cup log's `#`
   header records `dose continued from: <path>`, the totals, and the gap answer, via a
   new `CupSessionWriter` keyword `continuation: dict | None`.

The stop guard: if automatic insertion is running, `MainWindow` stops it through the
tab's confirmed stop; declining keeps the session running. On `session_stopped`:
`cup_log.close()`.

### C7. The words on screen

Human-readable text only: labels, buttons, group titles, dialog text, tooltips, the
cup log's `#` header comments and marker `details` text. **Python identifiers, CSV column
names and settings-file JSON keys keep their names.** Analysis scripts read those, and
`CONTEXT.md` "Known collisions" records the mapping.

| Before | After |
|---|---|
| Arm Cycle / Disarm | Start automatic cup insertion |
| Stop Cycle | Stop automatic cup insertion |
| Sampling Cycle (group title) | Automatic cup insertion |
| Cannot arm: <reason> | Cannot start automatic cup insertion: <reason> |
| Arm threshold | Run start current |
| Release threshold | Run end current |
| (any remaining "armed" status text) | running |

## Testing Decisions

Tests check behaviour an operator would notice: a file on disk, a row in it, a number on
screen. They do not check private attributes.

### Pure, no Qt, no hardware

- `log_rollover`: 23:59:59 to 00:00:00 crosses; 00:00:00 to 23:59:59 the same day does
  not; 31 Oct to 1 Nov gives a new month folder; 31 Dec to 1 Jan gives a new year;
  the November daylight-saving fall-back hour (01:30 occurring twice) does not cross;
  `unused_path` returns `_2` then `_3` when names exist.
- `VacuumMonitorLog` with `tmp_path` and given times: 10 states either side of midnight
  give two files in the right month folders; the second file's first data row is the
  first reading at or after 00:00:00; total rows across both files equal states written
  (none lost, none duplicated); each file has the `#` header; after `stop()`, writes
  create nothing, including across midnight; `start()` opens a new file on the next write;
  a month-end crossing creates the second folder.
- `VacuumLogger` with `file_stem="vacuum"` twice in one folder gives `vacuum.csv` and
  `vacuum_2.csv`, and the first is untouched.
- `DoseAccumulator`: a manual insertion between two automatic ones reduces the second
  hold interval by exactly the manual cup-in time and leaves the charge from the first
  unchanged; two overlapping exclusions subtract their union; an exclusion outside the
  interval changes nothing; seeding from totals then recording one insertion equals the
  same sequence in one unbroken accumulator, to floating-point tolerance only where the
  arithmetic genuinely differs (state the tolerance's origin in the test).
- `read_dose_totals` on a file written by a real `CupSessionWriter`: returns the last
  counted row's totals; ignores manual rows after it; returns `None` with no counted
  rows. `find_previous_session_cup_log` skips folders without counted rows and the
  excluded folder.

### Through the real objects

- `SessionRecorder` with a fake camera: start session, `start_video`, stop, `start_video`
  again, stop session. Assert segment names do not repeat, the first segment file still
  exists, `frames.csv` holds rows from both runs under one header, `events.csv` has two
  `video_started` and two `video_stopped`, and `session.json` lists two runs.
  `start_video` before `start()` returns `False` and creates nothing.
- `SessionRecorder.on_vacuum_state` writes `vacuum.csv` in the session folder only while
  recording, and the monitoring log's files are identical with and without a
  session running.
- `VacuumTab` through the real `Beamline` vacuum signal: Stop Logging, then 5 more
  states, creates no file and no rows.
- `FaradayCupTab` with no cup log: drive a manual insertion through `tests/payloads.py`
  and assert no file exists under the cup folders; the automatic start button is
  disabled and shows the reason.
- With a session cup log: a manual insertion writes a summary row with
  `origin=manual`, `counted_in_dose=false`, and the dose labels do not change; an
  automatic insertion writes `origin=automatic`, `counted_in_dose=true`, and the charge
  increases.
- Stop with `_confirm_stop_automatic` replaced to return `False`: automatic insertion
  keeps running and nothing is written. Returning `True`: the cup is commanded out.
- Restart with `_ask_restart` replaced: `beam_on_during_gap=False` excludes the gap (the
  next hold interval is shorter by the gap); `True` does not; the restart row records the
  answer and the mode; `None` starts nothing.
- Closing the cup log while automatic insertion runs commands the cup out and the start
  button becomes disabled.
- `CupPanel` renders from `CupView`: `Not logging` in WARN with no log, the path with a
  log, the button disabled while a session owns the log.

### Regression

Every existing test in `tests/test_vacuum_logger.py`, the session recorder tests and the
Faraday cup tests passes unchanged, except where a test asserts behaviour this spec
deliberately changes (the auto-restart after Stop, a cup file existing at tab
construction, the `Record video` checkbox, `set_video_enabled`, the arm/disarm toggle,
the `Keep previous schedule` checkbox). Each such test is rewritten in place under the
same name to assert the new behaviour. A ticket that deletes one names it on a
`Deletes tests:` line.

## Out of Scope

- Splitting the existing 176 MB file. The operator is deleting it.
- Rollover for anything but the monitoring log: the session's `data.csv` and
  `vacuum.csv`, cup logs, the scope profile logger, and the `*.jsonl` histories.
- Video outside a session. The Camera tab's preview and Take Photo are unchanged.
- Renaming Python identifiers, CSV column names or settings-file keys (C7).
- Any change to cup-settings behaviour beyond replacing ticket 16's checkbox with the C5
  dialog and the C7 label text.
- Changing heartbeat interval, thresholds, settle window or any cup default.
- A time limit on dose continuation. There is none, by decision.

## Further Notes

Ordering: Part A and Part B tickets can start now and touch none of the files the
cup-settings set touches (`vacuum_tab.py`, `vacuum_logger.py`, the two new services,
`session_recorder.py`, `video_recorder.py`, `recording_panel.py`, `camera_tab.py`, one
connection in `app.py`). `DoseAccumulator` (C4, pure part) and `log_rollover` are also
untouched by cup-settings. Everything that edits `faraday_cup_tab.py` or
`cup_session_writer.py` is blocked by cup-settings 16, and those tickets chain rather
than run in parallel, for the reason cup-settings gives: the tab is ~1 900 lines.

`app.py` is edited by B1, C1 and C6. Those are small and in different places in
`MainWindow.__init__`, but should not be in flight at the same time.
