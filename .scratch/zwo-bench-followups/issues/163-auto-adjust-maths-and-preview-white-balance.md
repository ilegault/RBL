# 163: The auto-exposure and white-balance maths, and the Preview's white balance

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 162 (both add constants to `capture_config.py` and to `EXPECTED` in `tests/test_capture_config.py`)

**Spec:** `.scratch/zwo-bench-followups/spec.md` (D7, first three bullets; user stories 19-25)
**Binding:** `docs/adr/0008-zwo-camera-owned-by-a-capture-process.md` (decision 6: display adjustments live in the Preview only), `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/hardware/preview_stretch.py` (module docstring, `debayer_for_display` and its `_DEBAYER_CODES`, `stretch_preview`), `src/rbl/config/capture_config.py` (`PREVIEW_*`), `tests/test_preview_stretch.py` (test_never_modifies_input, test_linear_auto_flat_and_brightness), `tests/test_capture_config.py` (`EXPECTED`).

## What to build

Pure functions only. Nothing on screen changes yet: ticket 164 calls them from the Capture Process, and 165 adds the buttons.

1. `src/rbl/config/capture_config.py`, values exactly as below, and the same entries added to `EXPECTED`: `AUTO_EXPOSURE_TARGET_FRACTION = 0.5`, `AUTO_EXPOSURE_PERCENTILE = 99.5`, `AUTO_EXPOSURE_SATURATION_LEVEL = 0.98`, `AUTO_EXPOSURE_SATURATED_FRACTION = 0.001`, `AUTO_EXPOSURE_SATURATED_STEP = 0.25`, `AUTO_EXPOSURE_MAX_RATIO = 16.0`, `PREVIEW_WB_MIN = 0.25`, `PREVIEW_WB_MAX = 4.0`, `PREVIEW_WB_DEFAULT = 1.0`. Comment the target: half of full scale leaves headroom for a glow that brightens during a run.
2. New `src/rbl/hardware/auto_adjust.py` (NumPy and `rbl.config` only, fully typed, docstring with `WHY THIS EXISTS`: one-shot choices made by the operator's click, never continuous; the camera's own auto modes stay off). It never modifies its input.
   - `suggest_exposure_us(raw: np.ndarray, bit_depth: int, exposure_us: int, min_us: int, max_us: int) -> int`: `full = 2**bit_depth - 1`. If the fraction of pixels `>= AUTO_EXPOSURE_SATURATION_LEVEL * full` exceeds `AUTO_EXPOSURE_SATURATED_FRACTION`, then `ratio = AUTO_EXPOSURE_SATURATED_STEP`. Otherwise `black = percentile(raw, PREVIEW_AUTO_LOW_PCT)` and `signal = percentile(raw, AUTO_EXPOSURE_PERCENTILE) - black`; `ratio = (AUTO_EXPOSURE_TARGET_FRACTION * full - black) / signal` when `signal > 0`, else `AUTO_EXPOSURE_MAX_RATIO`; clamp `ratio` to `[1 / AUTO_EXPOSURE_MAX_RATIO, AUTO_EXPOSURE_MAX_RATIO]`. Return `round(exposure_us * ratio)` clamped to `[min_us, max_us]`.
   - `grey_world_gains(raw: np.ndarray, bayer_pattern: str) -> tuple[float, float]`: subtract `black = percentile(raw, PREVIEW_AUTO_LOW_PCT)` (as float, floored at 0). The red, green and blue sites per 2 × 2 cell (row, column) are: `"RG"`: R (0,0), G (0,1) and (1,0), B (1,1); `"BG"`: B (0,0), G (0,1) and (1,0), R (1,1); `"GR"`: G (0,0) and (1,1), R (0,1), B (1,0); `"GB"`: G (0,0) and (1,1), B (0,1), R (1,0). Return `(mean_G / mean_R, mean_G / mean_B)`, each 1.0 when its denominator is not positive, and clamped to `[PREVIEW_WB_MIN, PREVIEW_WB_MAX]`. An unknown pattern raises `ValueError`.
3. `preview_stretch.stretch_preview` gains keyword arguments `wb_r: float = PREVIEW_WB_DEFAULT` and `wb_b: float = PREVIEW_WB_DEFAULT`. On an `H×W×3` RGB image, multiply channel 0 by `wb_r` and channel 2 by `wb_b` on its private float copy, before the linear or auto mapping (so the auto percentiles see the balanced image). On a 2-D image they are ignored. Clamp both to the WB limits. The input stays untouched, as the docstring promises; add the white balance to the docstring.

## Acceptance criteria

- [ ] Test `test_exposure_suggestion_scales_signal_to_the_target` in `tests/test_auto_adjust.py` (new file): a 100 × 100 `uint16` array whose first 100 pixels are 1000 and the rest `1000 + S`, with `S = round((0.5 * 65535 - 1000) / 4)`. `suggest_exposure_us(raw, 16, 10_000, 32, 2_000_000_000)` is within 0.1 % of 40_000, and the input array is unchanged.
- [ ] Test `test_saturated_or_black_scenes_step_and_clamp` in `tests/test_auto_adjust.py`: 1 % of pixels at 65535 → 2_500 from 10_000; every pixel equal → 160_000 from 10_000 (maximum ratio); the same with `max_us=50_000` → 50_000; saturated at `exposure_us=100, min_us=32` → 32; and an 8-bit array with 1 % of pixels at 255 → a quarter.
- [ ] Test `test_grey_world_gains_read_each_bayer_pattern` in `tests/test_auto_adjust.py`, parametrised over `"RG"`, `"BG"`, `"GR"`, `"GB"`: a 200 × 200 mosaic with red sites 1100, green 2100, blue 4100, then 1 % of each site's pixels set to 100. It returns `(2.0, 0.5)` to within 1e-9. Swapping the red and blue values gives `(0.5, 2.0)`.
- [ ] Test `test_grey_world_gains_clamp_and_guard` in `tests/test_auto_adjust.py`: red sites at the black level give `wb_r == 1.0`; red at 1/10 of green gives `wb_r == PREVIEW_WB_MAX`; an unknown pattern raises `ValueError`.
- [ ] Test `test_stretch_preview_white_balances_its_copy_only` in `tests/test_preview_stretch.py`: an RGB `uint16` image with all three channels equal to 1000. `stretch_preview(img, 16, "linear", 1.0, wb_r=2.0, wb_b=0.5)` gives a red channel twice the green (±1 count) and a blue channel half (±1). The input equals its saved copy. With default arguments the output equals the output before this change (green-channel comparison). A 2-D image gives the same output with or without the WB arguments.

**What tests may fake:** nothing. Synthetic NumPy arrays only.

**Out of scope:** calling these functions from the Capture Process (164); buttons and persistence (165); applying anything to recorded data.

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile --max-worker-restart=0 tests/test_auto_adjust.py tests/test_preview_stretch.py tests/test_capture_config.py
```

Run pytest on the touched test files only (implement-ticket-parallel, section 4). CI runs the full suite on the PR.

## Comments

