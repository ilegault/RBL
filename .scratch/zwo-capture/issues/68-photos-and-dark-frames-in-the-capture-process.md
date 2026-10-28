# 68: Photos and Dark Frames in the Capture Process

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 66

**Spec:** `.scratch/zwo-capture/spec.md`, sections D2 (`photo`, `take_darks`), D7, D6 (`photo`,
`darks_saved`)
**Binding:** `docs/adr/0008-zwo-camera-owned-by-a-capture-process.md` (decision 6),
`docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/hardware/fits_format.py` (62); `src/rbl/capture/process.py` and
`recording.py` (65, 66); `CONTEXT.md` "Dark Frame".

## What to build

### `src/rbl/capture/stills.py` (new)

1. `photo_cards(info: CameraInfo, settings: CameraSettings, temperature_c: float,
   date_obs_utc: datetime) -> list[tuple[str, object, str]]`: `EXPTIME` (seconds, float),
   `GAIN`, `BAYERPAT`, `XOFFSET`, `YOFFSET` (ROI origin), `CCD-TEMP`, `DATE-OBS` (UTC ISO 8601
   with milliseconds, no `Z`, as FITS expects), `INSTRUME` (`"<model> <serial>"`).
2. `next_photo_number(folder: Path) -> int`: one more than the highest `NNN` among files named
   `zwo_photo_NNN_K.fits` in `folder`, or 0.
3. `dark_file_name(serial, exposure_us, gain, bit_depth, roi, when_utc) -> str`:
   `dark_exp<us>us_gain<g>_raw<bits>_x<x>y<y>w<w>h<h>_<YYYYmmddTHHMMSS>.fits`, placed by the
   caller under `<folder>/<serial>/`.
4. All files are written with `open(path, "xb")` so nothing is ever overwritten. A Dark Frame
   whose name already exists (two in the same second) gets `_2`, `_3` … before `.fits`.

### `src/rbl/capture/process.py`

5. `photo` (`not_open` if closed): takes the next `burst` acquired frames **regardless of keep
   1 in N**, without pausing the stream and without starting a segment. Each is written with
   `pack_fits` as `zwo_photo_<NNN>_<k>.fits` (`NNN` = `next_photo_number(folder)` once per
   burst, `k` = 0..burst−1), with `photo_cards`. When one of those frames is also written to
   the SER, its frame-log row's `photo` column carries the file name. Replies `ok` with `files`
   (absolute paths) and sends `event` `photo` (detail: the file names joined with `", "`).
   Creates `folder` if missing.
6. `take_darks` (`not_open` if closed, `recording` while recording): averages the next
   `frames` acquired frames in float64, writes the mean as `float32` with `pack_fits`, cards
   from `photo_cards` plus `("NCOMBINE", frames, "frames averaged")`, at
   `<folder>/<serial>/<dark_file_name(...)>`; replies `ok` with `file` (absolute) and sends
   `event` `darks_saved` (detail: file name).

## Acceptance criteria

`tests/test_capture_process.py` (Seam A) unless noted; ROI 64 × 32, exposure 10000 µs. FITS
files are parsed with a test-side reader (`>i2` + `BZERO`, `>f4`), not `fits_format`.

- [ ] `test_photo_burst_during_recording`: while recording with keep 1 in 3, `photo` burst 3 →
  three files `zwo_photo_000_0..2.fits`; their pixels are FakeCamera frames for three
  **consecutive** *k* (recovered from pixel (0, 0); consecutive proves keep 1 in N was
  ignored); `EXPTIME == 0.01`, `XOFFSET == 0`, `BAYERPAT == 'RG'`; after `stop_recording`
  there is still exactly one segment; any of those frames that also appears in `frames.csv`
  has its file name in the `photo` column, and the others have it empty.
- [ ] `test_photo_numbering_continues`: a second `photo` into the same folder produces
  `zwo_photo_001_0.fits`; a pre-existing `zwo_photo_007_0.fits` makes the next one
  `zwo_photo_008_0.fits`; no existing file is modified (mtime and bytes unchanged).
- [ ] `test_take_darks`: while recording → `error` `recording`; not recording, `take_darks`
  frames 4 → one `float32` FITS at `<folder>/FAKE0001/dark_exp10000us_gain100_raw16_x0y0w64h32_*.fits`
  whose data equals, within 1e-3, the float64 mean of the FakeCamera frames for four
  consecutive *k* starting at some `k0` (the test searches `k0` in 0..4095 and requires a match),
  and `NCOMBINE == 4`; an `event` `darks_saved` arrived.
- [ ] `test_next_photo_number_and_dark_names` (in `tests/test_capture_stills.py`, pure, new): `next_photo_number` on an empty folder → 0,
  with `zwo_photo_002_0.fits` and `zwo_photo_010_3.fits` → 11, ignoring `photo_005.png`;
  `dark_file_name` gives the exact expected string for a fixed `datetime`; writing the same
  dark name twice produces `..._2.fits` and leaves the first file unchanged.
- [ ] `test_stills_need_an_open_camera`: `photo` and `take_darks` before `open` reply `not_open`.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile

Soft type-gate count must not rise above `tools/mypy_ratchet.txt`.

## Out of scope

- The beamline sidecar JSON next to each photo (the application writes it; ticket 71).
- Dark Frame subtraction or any analysis.
- Any `photo` from the USB camera path.

## Comments
