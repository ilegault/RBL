# 162: Exposure and gain sliders that apply on release

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/zwo-bench-followups/spec.md` (D6; user stories 12-18)
**Binding:** `docs/adr/0008-zwo-camera-owned-by-a-capture-process.md` (decision 6: a change to exposure or gain starts a new Segment), `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/gui/widgets/zwo_controls.py` (module docstring; row 1 in `__init__`; `_on_exposure_edited`, `_on_gain_changed`; `_render`'s "Control ranges" and "Display values with signals blocked" sections), `src/rbl/gui/widgets/inputs.py` (`NoScrollSlider`), `src/rbl/config/capture_config.py`, `tests/test_capture_config.py` (`EXPECTED`), `tests/test_zwo_controls.py` (the `recorder_and_widget` fixture, test_values_shown_come_from_the_reply, test_rows_are_packed).

## Why

Exposure and gain are number boxes only. The operator asked for an easier way to adjust them. A slider that sent a setting while dragging would start dozens of Segments in one drag. AGENTS.md (section 7) forbids mouse-wheel changes to setpoints.

## What to build

1. `src/rbl/config/capture_config.py`: `ZWO_EXPOSURE_SLIDER_STEPS = 1000`. Add it to `EXPECTED` in `tests/test_capture_config.py`.
2. `zwo_controls.py`, two pure module-level functions (requirement: they stay pure functions outside the widget, so the mapping is tested without Qt):
   - `slider_to_exposure_us(pos: int, min_us: int, max_us: int) -> int`: with `lo = log(max(min_us, 1))` and `hi = log(max_us)`, return `round(exp(lo + clamp(pos, 0, STEPS) / STEPS * (hi - lo)))`, clamped to [min_us, max_us]. When `max_us <= min_us`, return `min_us`.
   - `exposure_us_to_slider(exposure_us: int, min_us: int, max_us: int) -> int`: the inverse, rounded and clamped to [0, STEPS]. When `max_us <= min_us`, return 0.
3. Row 1: add `self.slider_exposure = NoScrollSlider(Qt.Orientation.Horizontal, self)` (range 0..`ZWO_EXPOSURE_SLIDER_STEPS`) right after the exposure `ms` label, and `self.slider_gain = NoScrollSlider(...)` right after the gain box. Call `setTracking(False)` on both, and give both a stretch factor of 1.
4. Signals:
   - `slider_exposure.sliderMoved(pos)` shows `slider_to_exposure_us(pos, …) / 1000` in `spin_exposure_ms`, with that box's signals blocked;
   - `slider_exposure.valueChanged(pos)`, which with tracking off fires on release or a keyboard step, calls `self._recorder.set_zwo_settings(exposure_us=slider_to_exposure_us(pos, …))` once;
   - `slider_gain.sliderMoved` shows the value in `spin_gain` (signals blocked), and `slider_gain.valueChanged` calls `set_zwo_settings(gain=pos)`.
5. `_render`: keep the Exposure range in `self._exposure_min_us` / `self._exposure_max_us` (from `controls["Exposure"]`) and `slider_gain.setRange` from `controls["Gain"]`. Set both slider positions from the confirmed settings with each slider's signals blocked (requirement: otherwise a render triggers `valueChanged` and sends a setting nobody chose). Both sliders are enabled exactly when the number boxes are.
6. Update the module docstring: sliders apply on release because each change starts a Segment; the wheel is ignored (no-scroll slider).

## Acceptance criteria

- [ ] Test `test_exposure_slider_is_logarithmic_and_round_trips` in `tests/test_zwo_controls.py` (no Qt objects): with range 32..2_000_000_000, position 0 → 32 and position 1000 → 2_000_000_000; position 500 is within 0.5 % of `round(sqrt(32 * 2e9))`; `exposure_us_to_slider(slider_to_exposure_us(p))` equals p for p in 0, 1, 250, 500, 999, 1000; out-of-range positions and exposures clamp.
- [ ] Test `test_dragging_the_exposure_slider_sends_one_setting_on_release` in `tests/test_zwo_controls.py`: camera open (fixture), `rec.set_zwo_settings` wrapped through `monkeypatch.setattr` to record its calls and pass them through. Press with `QTest.mousePress` at the handle centre (`QStyle.subControlRect(QStyle.ComplexControl.CC_Slider, opt, QStyle.SubControl.SC_SliderHandle, slider)`), `QTest.mouseMove` three times to the right, then `QTest.mouseRelease`. During the moves, `spin_exposure_ms` changes and the recorder has 0 calls. After release, it has exactly 1 call, `exposure_us=slider_to_exposure_us(<final position>, …)`. `wait_until` the confirmed `state()["zwo_settings"]["exposure_us"]` equals the snapped value, and `slider_exposure.value() == exposure_us_to_slider(<confirmed>)`.
- [ ] Test `test_dragging_the_gain_slider_sends_one_setting_on_release` in `tests/test_zwo_controls.py`: the same drag on `slider_gain`. 0 calls while moving, then exactly 1 call with `gain=<final position>`, and the confirmed gain shows in `spin_gain` and on the slider.
- [ ] Test `test_mouse_wheel_never_changes_exposure_or_gain` in `tests/test_zwo_controls.py`: send a `QWheelEvent` (angle delta 0, 120) with `QApplication.sendEvent` to each of the two sliders and the two number boxes. No value changes, and the wrapped `set_zwo_settings` records 0 calls.
- [ ] Test `test_sliders_follow_open_and_confirmed_settings` in `tests/test_zwo_controls.py`: both sliders are disabled before `connect_zwo()`. After open they are enabled, `slider_gain.value()` equals the confirmed gain, and `slider_exposure.value()` equals `exposure_us_to_slider(<confirmed exposure>, <Exposure min>, <Exposure max>)`. Opening sends no extra `set_zwo_settings` call from the sliders.

**What tests may fake:** the camera (the fake Capture Process child). The widget, the recorder and the mouse and wheel events are real; never call the slot methods directly.

**Out of scope:** mouse-wheel adjustment anywhere; sliders for bit depth, ROI or keep 1 in N; the Auto buttons (ticket 165); the Overview layout beyond row 1.

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile --max-worker-restart=0 tests/test_zwo_controls.py tests/test_capture_config.py
```

Run pytest on the touched test files only (implement-ticket-parallel, section 4). CI runs the full suite on the PR.

## Comments

