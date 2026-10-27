# 64: Preview stretch and debayer, ZWO paths, and the persisted ZWO settings

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 63

**Spec:** `.scratch/zwo-capture/spec.md`, sections D3 ("Defaults"), D8, D9 (`zwo_sdk_dll_path`),
"Where the code lives"
**Binding:** `docs/adr/0008-zwo-camera-owned-by-a-capture-process.md` (decision 6: display
stretch exists only in the Preview), `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:**
- `src/rbl/config/cup_settings_store.py`: the whole module. `zwo_settings_store.py` copies its
  shape: `STORE_PATH`, a frozen settings dataclass, `SettingsLoadWarning`,
  `validate_settings`, `load_settings`, `save_settings`, and how a bad value becomes a warning
  plus the default.
- `tests/test_cup_settings_store.py`: every test in it; the new store's tests mirror the ones
  that apply.
- `tests/conftest.py`: `_never_touch_the_real_cup_settings_store`.
- `src/rbl/config/paths.py`.

## What to build

### `src/rbl/hardware/preview_stretch.py` (new) — pure

1. Module docstring with `WHY THIS EXISTS`: the Preview is for watching, never for intensity
   (CONTEXT.md "Preview"); every function here returns a new array and **never modifies its
   input**, because the input is the frame that may also be written to the Master Recording.
2. `debayer_for_display(raw: np.ndarray, bayer_pattern: str) -> np.ndarray`: 2-D `uint8` or
   `uint16` mosaic → H×W×3 array of the same dtype in **RGB** channel order, with
   `cv2.cvtColor` and the code from one table keyed by pattern:
   `"RG" → cv2.COLOR_BayerRGGB2RGB`, `"GR" → cv2.COLOR_BayerGRBG2RGB`,
   `"GB" → cv2.COLOR_BayerGBRG2RGB`, `"BG" → cv2.COLOR_BayerBGGR2RGB`. Unknown pattern →
   `ValueError`. (Use the four-letter aliases, not OpenCV's older two-letter names, whose
   letters are offset from the sensor's and are a known source of swapped colours.)
3. `downscale_for_display(image: np.ndarray, max_width: int) -> np.ndarray`: unchanged copy if
   already ≤ `max_width` wide, else `cv2.resize` with `INTER_AREA` keeping the aspect ratio.
4. `stretch_preview(image: np.ndarray, bit_depth: int, mode: str, brightness: float) ->
   np.ndarray` (`uint8`, same shape), as spec D8: `linear` maps 0..2^bit_depth − 1 onto
   0..255 then multiplies by brightness; `auto` maps the `PREVIEW_AUTO_LOW_PCT` percentile to
   0 and the `PREVIEW_AUTO_HIGH_PCT` percentile to 255 (percentiles over all values, all
   channels), then multiplies by brightness; a flat image maps to all 0; brightness is clamped
   to `PREVIEW_BRIGHTNESS_MIN..PREVIEW_BRIGHTNESS_MAX`; the result is clipped to 0..255.
   Unknown mode → `ValueError`. Compute in float64 on a copy.

### `src/rbl/config/paths.py`

5. Add `ZWO_SETTINGS_STORE: Path = CONFIG_DIR / "zwo_camera.json"` and
   `ZWO_DARKS_DIR: Path = DATA_DIR / "zwo_darks"`, next to their neighbours, with one comment
   line each.
6. Add the pure function `zwo_sdk_dll_path(frozen: bool, bundle_dir: Path | None,
   repo_root: Path, env: Mapping[str, str]) -> Path`: `Path(env["RBL_ZWO_SDK_DLL"])` when that
   key is set and non-empty; else, when `frozen`, `bundle_dir / "ASICamera2.dll"`; else
   `repo_root / "vendor" / "zwo" / "ASICamera2.dll"`. It never checks whether the file exists
   (the caller does; ticket 70). Pure: callers pass `sys.frozen`, `sys._MEIPASS` and
   `os.environ` in, so the function can be tested for all three layouts on one machine.

### `src/rbl/config/zwo_settings_store.py` (new)

7. Same shape as `cup_settings_store.py`. `STORE_PATH = ZWO_SETTINGS_STORE`.
   `@dataclass(frozen=True) class ZwoSettings` with fields and defaults, every default taken
   from `capture_config` (no literal defaults in the module, as `test_no_default_literals_in_module`
   checks for the cup store):
   `camera_kind: str = "usb"`, `exposure_us: int = ZWO_EXPOSURE_US_DEFAULT`,
   `gain: int = ZWO_GAIN_DEFAULT`, `bit_depth: int = ZWO_BIT_DEPTH_DEFAULT`,
   `roi: tuple[int, int, int, int] | None = None` (None means full sensor),
   `keep_one_in_n: int = 1`, `preview_mode: str = "auto"`,
   `brightness: float = PREVIEW_BRIGHTNESS_DEFAULT`.
   The default `camera_kind` is `"usb"` **so the application behaves exactly as today until the
   operator picks the ZWO camera.** (The `1`, `"usb"` and `"auto"` defaults may be module-level
   named constants in `capture_config.py`; add them there if the literal-defaults test needs it.)
8. `validate_settings(raw: dict)`, `load_settings(path=None)`, `save_settings(settings,
   path=None)` with the cup store's warning behaviour. Valid values: `camera_kind` in
   `{"usb", "zwo"}`; `exposure_us` int ≥ 1; `gain` int ≥ 0; `bit_depth` 8 or 16; `roi` null or
   an object of four ints with width ≥ 8 and height ≥ 2 (stored as JSON
   `{"x", "y", "width", "height"}`); `keep_one_in_n` 1..`CAPTURE_KEEP_ONE_IN_N_MAX`;
   `preview_mode` in `{"auto", "linear"}`; `brightness` finite and within the brightness
   limits. Each invalid key gives one `SettingsLoadWarning` and that key's default.

### `tests/conftest.py`

9. Add an autouse fixture `_never_touch_the_real_zwo_settings_store`, modelled on
   `_never_touch_the_real_cup_settings_store`, that points `zwo_settings_store.STORE_PATH` at a
   file under `tmp_path`. A requirement, not a nicety: later tickets construct a
   `SessionRecorder` in tests, and it loads and saves this store.

## Acceptance criteria

- [ ] `test_never_modifies_input` (in `tests/test_preview_stretch.py`): for each of the three
  functions, a `uint16` input compared with a `.copy()` taken before the call is unchanged
  after it (`np.array_equal`), and the returned array is not the input object.
- [ ] `test_linear_auto_flat_and_brightness` (in `tests/test_preview_stretch.py`): linear maps 65535
  (bit depth 16) and 255 (bit depth 8) to 255 and 0 to 0; auto on a ramp maps its 0.5th
  percentile to 0 and its 99.5th to 255; a flat image gives all zeros in auto mode;
  brightness 10.0 behaves exactly like 4.0 and 0.01 like 0.25; output dtype is `uint8`.
- [ ] `test_debayer_puts_red_in_the_red_channel` (in `tests/test_preview_stretch.py`) (parametrized
  over the four patterns): a 16 × 16 `uint16` mosaic that is 4000 at the red site of each 2 × 2
  cell for that pattern (row, column within the cell: `RG` (0, 0), `GR` (0, 1), `GB` (1, 0),
  `BG` (1, 1)) and 0 elsewhere debayers to an image whose channel 0 mean exceeds 10×
  the means of channels 1 and 2; unknown pattern raises `ValueError`.
- [ ] `tests/test_capture_command_line.py` (new):
  `test_zwo_sdk_dll_path_env_frozen_and_source` covers the env override (wins in both frozen
  and source layouts), frozen → `bundle_dir / "ASICamera2.dll"`, source →
  `repo_root / "vendor" / "zwo" / "ASICamera2.dll"`, and an empty `RBL_ZWO_SDK_DLL` is ignored.
- [ ] `tests/test_zwo_settings_store.py` (new) mirrors the applicable tests of
  `tests/test_cup_settings_store.py`: defaults, a valid round trip through `save_settings` and
  `load_settings` on a `tmp_path` file (including a non-None `roi`), one parametrized case per
  invalid key giving exactly one warning and the default, a corrupt (non-JSON) file giving all
  defaults and a warning, module purity (no `PySide6` import), and no default literals.
  Plus `test_conftest_redirects_the_store`: inside a test, `zwo_settings_store.STORE_PATH` is
  not the real `paths.ZWO_SETTINGS_STORE` and does not lie under `Path.home() / ".config"`.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile

All three source files are under hard type-gate prefixes: any mypy error fails the build.

## Out of scope

- JPEG encoding and the preview cadence (ticket 65).
- Any GUI for these settings (tickets 73 and 74).
- Changing any existing path in `paths.py`.

## Comments
