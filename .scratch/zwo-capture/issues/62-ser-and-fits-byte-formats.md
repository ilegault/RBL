# 62: SER and FITS byte formats, as two pure modules

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/zwo-capture/spec.md`, sections D4 ("SER") and D7 ("FITS")
**Binding:** `docs/adr/0008-zwo-camera-owned-by-a-capture-process.md` (decisions 4 and 6),
`docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `CONTEXT.md` section "Camera" (Master Recording, Segment, Dark Frame);
`src/rbl/hardware/cup_status.py` module docstring, as the `WHY THIS EXISTS` style to match.

## What to build

The two file formats the Capture Process writes, as bytes, with no file handles, no Qt and
no clock. Later tickets write files with them; this ticket only makes and proves the bytes.

### `src/rbl/hardware/ser_format.py` (new)

1. Module docstring with `WHY THIS EXISTS`: SER is the Master Recording's container; its
   layout is the published SER v3 layout; a compiled Capture Process must produce the same
   bytes (ADR 0008 decision 4).
2. Constants: `SER_HEADER_BYTES = 178`, `SER_FILE_ID = b"LUCAM-RECORDER"`,
   `SER_LITTLE_ENDIAN_FLAG = 1` (one named constant; bench ticket 79 confirms it, and it is
   the only thing that changes if SER Player shows swapped bytes),
   `SER_FRAME_COUNT_OFFSET = 38` (byte offset of `FrameCount`, used by the writer in ticket 66
   to refresh the count in place).
3. `pack_header(*, color_id: int, width: int, height: int, pixel_depth: int, frame_count: int,
   observer: str, instrument: str, telescope: str, date_local: int, date_utc: int) -> bytes`:
   little-endian, in order: `FileID` (14 bytes), `LuID` int32 = 0, `ColorID` int32,
   `LittleEndian` int32 = `SER_LITTLE_ENDIAN_FLAG`, `ImageWidth`, `ImageHeight`,
   `PixelDepthPerPlane`, `FrameCount` (int32 each), `Observer`, `Instrument`, `Telescope`
   (40 bytes each, ASCII, space-padded, longer strings truncated), `DateTime`, `DateTime_UTC`
   (int64 each). Raises `ValueError` when `pixel_depth` is not 8 or 16, or `width`/`height`
   is below 1.
4. `pack_trailer(timestamps: Sequence[int]) -> bytes`: one little-endian int64 per frame.
5. `color_id_for_bayer(pattern: str) -> int`: `"RG"→8`, `"GR"→9`, `"GB"→10`, `"BG"→11`;
   any other string raises `ValueError`.
6. `ticks_from_datetime(dt: datetime) -> int`: count of 100 ns ticks since 0001-01-01 00:00
   of `dt`'s own date and time fields. No timezone conversion: the caller passes a local time
   for `DateTime` and a UTC time for `DateTime_UTC`.

### `src/rbl/hardware/fits_format.py` (new)

7. Module docstring with `WHY THIS EXISTS`: FITS is used where one image is one file
   (photos, Dark Frames); Fiji and astronomy tools read it.
8. `pack_fits(data: np.ndarray, cards: list[tuple[str, object, str]]) -> bytes`: one primary
   HDU. `data` must be 2-D with dtype `uint8`, `uint16` or `float32`; anything else raises
   `ValueError`.
   - Mandatory cards first, in this order: `SIMPLE = T`, `BITPIX`, `NAXIS = 2`,
     `NAXIS1` = columns (width), `NAXIS2` = rows (height). `uint8` → `BITPIX = 8`;
     `uint16` → `BITPIX = 16` then `BZERO = 32768`, `BSCALE = 1`, data stored as
     `value - 32768` in int16; `float32` → `BITPIX = -32`. Then the caller's cards, then `END`.
   - Fixed format: keyword left-justified in columns 1-8; `= ` in columns 9-10; logical,
     integer and float values right-justified ending in column 30; strings single-quoted
     starting in column 11 and padded with spaces to at least 8 characters inside the quotes;
     then ` / comment` if the comment is non-empty. Every card is exactly 80 ASCII characters.
     A keyword longer than 8 characters, non-ASCII text, or a value that is not `bool`, `int`,
     `float` or `str` raises `ValueError`.
   - Header padded with spaces and data padded with zero bytes, each to a multiple of 2880.
     Data is big-endian.

## Acceptance criteria

All in new test files. **The tests parse the bytes with readers written inside the test files
from the published layouts** (`struct`, `numpy.frombuffer` with explicit `>`/`<` dtypes),
never with `ser_format` or `fits_format` themselves; a test that decodes with the module under
test proves nothing.

- [ ] `test_header_fields_round_trip` (in `tests/test_ser_format.py`): `pack_header(...)` with
  distinctive values is exactly 178 bytes, and the test-side reader recovers every field,
  including `LuID == 0`, `LittleEndian == SER_LITTLE_ENDIAN_FLAG`, the space padding of
  `Observer`/`Instrument`/`Telescope`, and both int64 dates; `FrameCount` read at
  `SER_FRAME_COUNT_OFFSET` equals the value passed.
- [ ] `test_frames_and_trailer_round_trip` (in `tests/test_ser_format.py`): for `pixel_depth` 16 and 8,
  header + 5 frames (each frame's bytes are `frame.astype("<u2").tobytes()` /
  `frame.tobytes()`) + `pack_trailer` written to a `tmp_path` file; the test reader returns
  every pixel of every frame equal to the source arrays and every trailer timestamp equal to
  the input list.
- [ ] `test_color_id_and_ticks` (in `tests/test_ser_format.py`): all four Bayer patterns map as in
  step 5 and `"XX"` raises `ValueError`; `ticks_from_datetime(datetime(1, 1, 1)) == 0`;
  `ticks_from_datetime(datetime(1970, 1, 1)) == 621355968000000000`; one microsecond more adds
  exactly 10; an aware UTC datetime gives the same count as the naive one with the same fields.
- [ ] `test_uint16_round_trip_and_layout` (in `tests/test_fits_format.py`): a 3 × 5 `uint16` array
  containing 0 and 65535; total length is a multiple of 2880; the header's first five cards
  are `SIMPLE`, `BITPIX`, `NAXIS`, `NAXIS1`, `NAXIS2` in that order with `NAXIS1 == 5`; the
  card `"SIMPLE  =                    T"` padded to 80 characters is the first card exactly; an
  `END` card is present and everything after it in the header block is spaces; the test-side
  reader (`>i2` plus `BZERO`) recovers the array exactly, including 0 and 65535; data padding
  bytes are zero.
- [ ] `test_uint8_float32_and_cards` (in `tests/test_fits_format.py`): a `uint8` array gives
  `BITPIX = 8` and no `BZERO`; a `float32` array gives `BITPIX = -32` and round-trips through
  `>f4`; caller cards `("EXPTIME", 0.02, "s")`, `("GAIN", 100, "")`, `("BAYERPAT", "RG", "")`,
  `("SIMPLE2", True, "")` appear after the mandatory cards, with the string value starting in
  column 11 inside quotes and the integer ending in column 30; `ValueError` for a 9-character
  keyword, a non-ASCII string, a `list` value, a 3-D array and an `int32` array.
- [ ] `test_imports_are_stdlib_and_numpy_only` (one in each test file): the module under test
  imports only the standard library and `numpy`, read from its `import` lines with `ast`.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile

Both files are under `rbl.hardware`, a **hard** type-gate prefix: any mypy error in them fails
the build. Annotate every function fully. Do not add `# type: ignore` (the config has
`warn_unused_ignores = true`).

## Out of scope

- Any file handle or writer class (ticket 66 holds the SER writer; ticket 68 writes FITS files).
- Reading SER or FITS anywhere in `src/`.
- FITS extensions, compression, or any keyword beyond those named here.

## Comments
