# 66: The Capture Process records: SER segments, the frame log and the settings records

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 62, 65

**Spec:** `.scratch/zwo-capture/spec.md`, sections D2 (`start_recording`, `stop_recording`,
`beamline`), D4 (all of it), D5 (threads 3-4, keep 1 in N), D6
**Binding:** `docs/adr/0008-zwo-camera-owned-by-a-capture-process.md` (decision 6: never
converted, a settings change starts a new segment), `docs/adr/0004-monitoring-log-and-sessions.md`
(decision 7: numbering continues across video runs in a session),
`docs/adr/0003-commanded-and-confirmed-cup-position.md`,
`docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/hardware/ser_format.py` (62); `src/rbl/capture/process.py` and
`camera.py` (65); `tests/capture_child.py` (65); `src/rbl/services/snapshot_json.py`
`dump_json` (the temp-name-then-rename technique to copy; **do not import it**, the Capture
Process may not import `rbl.services`); `CONTEXT.md` "Frame log", "Segment", "Settings record".

## What to build

### `src/rbl/capture/recording.py` (new)

1. `class SerSegmentWriter`: opens `<folder>/zwo/segment_NNN.ser` (3-digit, zero-padded) in
   exclusive-create mode, writes `ser_format.pack_header(...)` with `frame_count=0`,
   `observer="RBL"`, `instrument=f"{model} {serial}"`, `telescope=""`, `color_id` from the Bayer
   pattern, `date_local`/`date_utc` from the segment's open time;
   `append(frame: np.ndarray, utc_ticks: int)` writes the frame's bytes exactly as delivered
   (`uint16` as `<u2`, `uint8` as is) and remembers the timestamp; `bytes_written` property;
   `refresh_header()` rewrites the int32 at `SER_FRAME_COUNT_OFFSET` with the current count
   and flushes; `close()` refreshes the count and writes `pack_trailer`. The writer calls
   `refresh_header()` itself whenever `CAPTURE_SER_HEADER_REFRESH_S` has passed since the last
   refresh (crash safety, spec D4).
2. `class FrameLog`: appends to `<folder>/zwo/frames.csv`; writes the three `#` comment lines
   and the header row of spec D4 **only when the file is new**; one row per written frame;
   flushes after every row.
3. `write_settings_record(path: Path, record: dict) -> None`: JSON with indent 2 to
   `<path>.part`, then `os.replace` onto `path`. The record has exactly the keys of spec D4's
   example; `focus_preset` is always `null`.

### `src/rbl/capture/process.py`

4. A bounded `queue.Queue(maxsize=CAPTURE_WRITE_QUEUE_FRAMES)` and a writer thread. The
   acquisition thread stamps each frame with its arrival time (UTC from the process's own
   `time.perf_counter()` + `datetime.now(timezone.utc)` anchor taken at start-up, never a fresh
   `datetime.now()` per frame), its running acquired index and the settings generation, stores
   it as the latest frame under a lock, and — while recording and when
   `acquired_index % keep_one_in_n == 0` — puts it with `put_nowait`. **A full queue drops the
   frame and increments `frames_dropped_queue`; the acquisition thread never blocks on the
   disk.** Keep 1 in N counts from the acquired index, not from the recording start.
5. `start_recording`: `error` `not_open` if closed, `already_recording` if recording. Creates
   `<folder>/zwo/`, remembers `session_t0_utc`, `first_segment_index`, `first_frame_index`,
   replies `ok`, sends `event` `recording_started`. The writer opens a segment on the first
   frame, writes the settings record when the segment opens (with `closed_utc`,
   `closed_reason` null and `frames` 0) and sends `event` `segment_opened` (detail: file name).
6. The writer closes the current segment and opens the next when a frame's generation differs
   from the segment's: `closed_reason` `"settings_change"`. A `set` that changes only
   `keep_one_in_n` also increments the generation (the settings record carries it) but does not
   restart the stream. On close it rewrites the settings
   record (frames, both drop counts, `closed_utc`, `closed_reason`,
   `sensor_temperature_c_close`) and sends `event` `segment_closed` with detail
   `"<file>, <reason>, <frames>"`.
7. Frame-log row per written frame: `frame_index` (session-wide, continuing from
   `first_frame_index`), `wall_utc` (ISO 8601 with milliseconds and `Z`, the same format as
   `SessionRecorder._derive_wall`), `t_rel_s` (seconds from `session_t0_utc`, 6 decimals),
   `segment`, `segment_frame` (0-based within the segment), `cup_position`, `alumina_position`
   (always empty), `photo` (empty in this ticket).
8. `beamline` command: stores `cup_position` (and `alumina_position`, unused); replies `ok`.
   `cup_position` is `"UNKNOWN"` until the first `beamline` message.
9. `stop_recording`: `not_recording` if not recording; otherwise drains the queue, closes the
   segment with `closed_reason` `"stop"`, replies `ok` with `next_segment_index`,
   `next_frame_index` and `segments` (file names written in this run), sends `event`
   `recording_stopped` (detail `"stop"`). `close` while recording replies `error`
   `recording`.
10. `status` while recording carries `recording` true, `segment`, `frames_written`,
    `frames_dropped_queue`, and `disk_free_bytes` of the recording folder.

## Acceptance criteria

All in `tests/test_capture_process.py`, through `tests/capture_child.py`, recording into
`tmp_path`. Every test first sends `set` with `roi` `{0, 0, 64, 32}` and `exposure_us` 10000 so
files stay small. A frame's acquired index *k* is recovered from its pixel (0, 0)
(`(k * 7) % 4096 == pixel >> 4`, which is invertible because 7 is odd), and **every written
frame is compared pixel for pixel with FakeCamera's formula for its *k*.** Parse SER files with
a test-side reader (`struct`), not with `ser_format`.

- [ ] `test_recording_writes_ser_frame_log_and_settings_record`: record ~2 s, stop. Exactly one
  `segment_000.ser`: header `FrameCount` equals the trailer length equals the number of
  frame-log rows; every frame equals the formula; trailer timestamps are non-decreasing;
  `frames.csv` has the three comment lines, the header, and rows with `segment_frame`
  0, 1, 2 …; `segment_000.json` has `closed_reason == "stop"`, `frames` equal to the row
  count, `settings.roi == {x: 0, y: 0, width: 64, height: 32}`, `focus_preset` null; the
  `stop_recording` reply has `next_segment_index == 1` and `next_frame_index ==` row count;
  `event`s `recording_started`, `segment_opened`, `segment_closed`, `recording_stopped` arrive
  in that order.
- [ ] `test_settings_change_mid_recording_starts_a_segment`: record, `set exposure_us` 12000,
  record, stop. Exactly two segments; each settings record's `exposure_us` matches what was in
  force (10000, then 12000); the first has `closed_reason == "settings_change"`; frame-log
  `segment` column is 0 then 1 with no row of segment 0 after a row of segment 1; an `event`
  `settings_changed` with detail `"exposure_us: 10000 -> 12000"` arrived.
- [ ] `test_keep_one_in_three`: `set keep_one_in_n` 3, record, stop; every written frame's *k*
  is divisible by 3 and consecutive written frames differ by exactly 3 (allowing gaps only
  where `frames_dropped_queue` in the settings record is non-zero, which with a 64 × 32 ROI it
  must not be: assert it is 0).
- [ ] `test_second_run_continues_numbering`: two video runs into the same folder, the second
  started with the first's `next_segment_index` / `next_frame_index`; the second run's first
  segment is `segment_001.ser`, `frames.csv` has one header and continuous `frame_index`.
- [ ] `test_beamline_cup_position_lands_in_the_frame_log`: rows before any `beamline` say
  `UNKNOWN`; after `beamline` `IN`, later rows say `IN`; after `OUT`, `OUT`. And
  `start_recording` while recording → `already_recording`, `stop_recording` while not →
  `not_recording`, `close` while recording → `recording`.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile

Soft type-gate count must not rise above `tools/mypy_ratchet.txt`.

## Out of scope

- Segment size limit, low disk, camera loss, heartbeat loss and shutdown endings (ticket 67).
- Photos and Dark Frames, and the `photo` column (ticket 68).
- Anything in the application.

## Comments
