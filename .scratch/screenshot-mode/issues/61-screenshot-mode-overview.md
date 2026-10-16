# 61: Screenshot mode — a dev-only launcher that fills the Overview with a canned mid-irradiation scene

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:**
- `src/rbl/gui/widgets/drag_panel.py`: module docstring, `DragPanel.__init__`, `PanelArea.add`,
  `PanelArea._apply_move`, `PanelArea._rebuild`.
- `src/rbl/gui/overview_tab.py`: `OverviewTab.__init__` (the six `self._panel_area.add(...)`
  calls), `attach_recorder`, `attach_cup_log`, `showEvent`, `_redraw_slits`, `_redraw_gaps`,
  `_redraw_amps`, `_redraw_phase_label`, `_redraw_timebase`.
- `src/rbl/gui/app.py`: `main()` and `MainWindow._on_connect_all`.
- `src/rbl/state/motor_control.py`: `ingest_motor_poll`.
- `src/rbl/state/labjack_link.py`: `ingest_labjack_window`.
- `src/rbl/state/funcgen_control.py`: `ingest_funcgen_readback`.
- `src/rbl/state/hv_interlock_link.py`: `set_interlock_gauges`, `_recompute_hv_interlock`.
- `src/rbl/state/setpoints.py`: `FuncGenSetpoints.update_axis`.
- `src/rbl/config/paths.py`: every path is computed from `Path.home()` / `RBL_LOG_ROOT`
  **at import time**.
- `tests/payloads.py`: `window_payload` (reused by the script, not copied).
- `tests/test_archive_pre_adr_0007.py`, lines 1-30: how a test imports from `scripts/`.

## Why

Isaac needs clean screenshots of a working Overview (talks, write-ups) without a beamline
attached. The Overview's default tile arrangement is also poor; a proper default is a
separate multi-ticket effort and is **not** this ticket. This ticket gives screenshot mode
its own arrangement, through a new public `PanelArea.arrange()` that the later default-layout
work can reuse.

Screenshot mode must be impossible to reach from normal operation. That is why it is a
script under `scripts/` that nothing in `src/` imports and PyInstaller never bundles, not a
flag, menu item or environment variable in the app.

## What to build

### A. `src/rbl/gui/widgets/drag_panel.py`

1. `DragPanel.__init__(self, content, stretch=0, name="", parent=None)`: new keyword `name: str`,
   stored as the public attribute `self.name`. Keep `parent` last so existing positional
   callers are unaffected.
2. New public method `PanelArea.column_names(self) -> list[list[str]]`: the `name` of every
   panel, column by column, top to bottom, skipping empty columns.
3. New public method `PanelArea.arrange(self, columns: list[list[str]]) -> None`:
   - Every panel currently in the area must appear exactly once across `columns`. If a name
     is unknown, repeated, or a current panel is missing, or any panel's `name` is `""`, raise
     `ValueError` naming the offending panel(s) **and leave the layout unchanged**. A layout
     must never be able to make a panel disappear.
   - Empty inner lists are dropped.
   - Otherwise set the columns to exactly that order and call `self._rebuild()`.
4. Add both methods to the module docstring's Usage block.

### B. `src/rbl/gui/overview_tab.py`

5. Pass `name=` to every `DragPanel(...)`, exactly these names:
   - `self.beam` → `"beam"`
   - `_build_slit_control_box()` → `"slits"`
   - `_build_current_box()` → `"current"`
   - `_build_vacuum_box()` → `"vacuum"`
   - `_build_funcgen_box()` → `"funcgen"`
   - `_build_hv_box()` → `"hv"`
   - in `attach_recorder` → `"recording"`
   - in `attach_cup_log` → `"cup"`
6. New read-only property `panel_area -> PanelArea` returning `self._panel_area`.
7. Do **not** change the default arrangement (the `col=` values). Add one sentence to the
   module docstring: panels are named so a layout can be applied by name with
   `PanelArea.arrange()`.

### C. `src/rbl/gui/app.py`

8. Extract from `main()` a new module-level function
   `make_application(argv: list[str]) -> QApplication` containing, in this order: the
   `QT_AUTO_SCREEN_SCALE_FACTOR` setdefault, `setHighDpiScaleFactorRoundingPolicy(PassThrough)`,
   `QApplication(argv)`, `setStyle("Fusion")`, and the whole palette block; it returns the app.
   `main()` keeps its logging setup, then calls `app = make_application(sys.argv)`. No
   behaviour change. Reason: the screenshot must look exactly like the real app, and a second
   copy of the palette would drift.

### D. `scripts/screenshot_overview.py` (new)

Module docstring with a `WHY THIS EXISTS` section stating: dev-only; never imported by `src/`;
never bundled; isolates every persistence path into a fresh temp folder; refuses to keep
feeding fake data once any real instrument reports; the HV interlock line is **not** faked
(see 15).

9. **No `rbl` import at module level.** Every `rbl` (and `tests.payloads`) import is inside a
   function. Module level may import only the standard library, `numpy` and `PySide6`. This is
   a requirement: `rbl.config.paths` freezes `Path.home()` at import, so isolation in 11 only
   works if no `rbl` module is loaded before it. At module top, insert the repo root
   (`Path(__file__).resolve().parent.parent`) into `sys.path` if absent.
10. Module constants, exactly:
    ```python
    SCREENSHOT_LAYOUT = [["beam", "slits", "current"],
                         ["funcgen", "hv"],
                         ["vacuum", "cup", "recording"]]
    SLIT_POSITIONS_MM = {"X+": 2.5, "X-": 2.5, "Y+": 2.0, "Y-": 2.0}
    SLIT_CURRENTS_A   = {"X+": 120e-9, "X-": 110e-9, "Y+": 95e-9, "Y-": 100e-9}
    AXIS_DRIVE        = {"X": (3.0, 500.0), "Y": (2.0, 100.0)}  # (peak V == peak kV, Hz)
    AMP_CURRENT_MONITOR_V = 0.25          # flat, every amplifier
    CHAMBER_GAUGE   = "vgc083:IG"
    CHAMBER_TORR    = 3.2e-7
    FORELINE_TORR   = 2.4e-3              # VGC083 channel "CG1"
    STREAM_SAMPLES  = 750                 # one FULL-profile window: 7 500 S/s x 0.1 s
    STREAM_PERIOD_S = 1.0 / 7500.0
    REPUSH_MS       = 1000                # < GAUGE_STALE_TIMEOUT_S (5 s) so the interlock stays fresh
    TITLE_SUFFIX    = " — SCREENSHOT MODE (simulated data)"
    ```
    500 Hz and 100 Hz are whole cycles per 750-sample window, so repeated identical windows
    are phase-continuous in the amp-trace history.
11. `isolate_environment(root: Path) -> None`: if any key in `sys.modules` equals `"rbl"` or
    starts with `"rbl."`, raise `RuntimeError("isolate_environment must run before any rbl import")`.
    Otherwise set `os.environ["HOME"]`, `os.environ["USERPROFILE"]` to `str(root)` and
    `os.environ["RBL_LOG_ROOT"]` to `str(root / "Desktop" / "RBL_log")`.
12. `@dataclass(frozen=True) class Scene` with fields: `motor_poll: dict`, `zeroed: bool`,
    `labjack_payload: dict`, `funcgen_connected: dict`, `funcgen_timebase: dict`,
    `funcgen_readback: dict`, `axis_setpoints: dict`, `vacuum: object`.
    `build_scene() -> Scene` (pure: no Qt, no clock except `vacuum.timestamp`), built as:
    - `motor_poll`: for axis letter `L` and slit `s` in `SC.AXIS_NAMES`:
      `{"pos": SC.mm_to_counts(L, SLIT_POSITIONS_MM[s]), "moving": False, "enabled": True,
      "switches": {"forward_switch": False, "reverse_switch": False, "home_switch": False}}`.
      `zeroed=True`.
    - `labjack_payload`: `tests.payloads.window_payload(volts, t=1.0, samples=STREAM_SAMPLES,
      sample_period=STREAM_PERIOD_S, waveforms=waves, profile="FULL")` where
      - `volts[ain]` for each `ain, slit` in `SC.LABJACK_CHANNEL_MAP`:
        `SC.LOG_AMP_V_AT_1NA + (SC.LOG_AMP_V_AT_1MA - SC.LOG_AMP_V_AT_1NA) * math.log10(SLIT_CURRENTS_A[slit] / 1e-9) / 6.0`
        (the exact inverse of `voltage_to_current`);
      - `volts[SC.AMP_CHANNEL_MAP[amp]["current"]] = AMP_CURRENT_MONITOR_V` for every amp;
      - `waves[SC.AMP_CHANNEL_MAP[amp]["voltage"]]`: with `t = np.arange(STREAM_SAMPLES) * STREAM_PERIOD_S`
        and `tri(f) = 2.0 * np.abs(2.0 * ((t * f) % 1.0) - 1.0) - 1.0`, the `+` amp of axis `a`
        gets `peak * tri(f)` and the `-` amp gets `-peak * tri(f)`, `(peak, f) = AXIS_DRIVE[a]`.
        (Monitor volts == kV at `VOLTAGE_MONITOR_KV_PER_VOLT = 1.0`.)
    - `funcgen_connected = {"A": True, "B": True}`; `funcgen_timebase = {"A": "INT", "B": "EXT"}`.
    - `funcgen_readback`: for each axis, for `key, phase in zip(AXIS_CHANNELS[axis], (0.0, 180.0))`:
      `{"shape": "RAMP", "freq": f, "amp": 2.0 * peak, "offset": 0.0, "phase": phase,
      "output": True, "load": "INFinity", "load_ohms": math.inf}`.
    - `axis_setpoints = {axis: {"amp_vpp": 2.0 * peak, "freq_hz": f, "output_on": True}}`.
    - `vacuum = VacuumState(timestamp=time.time(), vgc_readings=[VgcReading("IG", CHAMBER_TORR, "", "OK"),
      VgcReading("CG1", FORELINE_TORR, "", "OK")], vgc_connected=True, units_vgc="Torr")`.
13. `push_scene(beamline, scene: Scene) -> None`, in this order, only through Beamline's public
    surface: `beamline.ingest_motor_poll(scene.motor_poll, scene.zeroed)`;
    `beamline.ingest_labjack_window(scene.labjack_payload, active_profile="FULL")`;
    `beamline.ingest_funcgen_readback(scene.funcgen_connected, scene.funcgen_timebase, scene.funcgen_readback)`;
    `beamline.vacuum_changed.emit(dataclasses.replace(scene.vacuum, timestamp=time.time()))`.
    It does **not** emit `raw_window_ready` (nothing on the Overview needs raw volts, and the
    raw consumers are calibration/characterization runners). No scope data (beam FWHM shows "—").
14. `class ScreenshotFeed` (plain class, owns a `QTimer`):
    `__init__(self, beamline, scene, on_stop: Callable[[str], None] | None = None)`;
    `start()` pushes once, connects the watchers, starts the timer at `REPUSH_MS`;
    `push()` sets an internal "pushing" flag, calls `push_scene`, clears the flag in `finally`;
    `stop(reason: str)` stops the timer, disconnects its watchers, sets `active` False, calls
    `on_stop(reason)`; read-only property `active: bool`.
    Watchers: `motors_changed`, `logamps_changed`, `amps_changed`, `funcgens_changed`,
    `vacuum_changed` — any emission while **not** pushing calls
    `stop(f"real {signal name} data arrived")`; `labjack_connected` and `cup_connected` call
    `stop("a real instrument connected")` unconditionally. This is a requirement, not a
    nicety: once a real instrument reports, re-pushing fake positions over real ones on a live
    control panel is a hazard.
15. `start_screenshot_mode(win) -> ScreenshotFeed`:
    - `win.setWindowTitle(win.windowTitle() + TITLE_SUFFIX)`;
    - `win.overview_tab.connect_all_requested.disconnect()` (Connect All does nothing);
    - `win.beamline.set_interlock_gauges({CHAMBER_GAUGE})` (persists only into the isolated
      folder);
    - `win.beamline.funcgen_setpoints.update_axis(axis, **scene.axis_setpoints[axis])` for X, Y;
    - `win.overview_tab.panel_area.arrange(SCREENSHOT_LAYOUT)`;
    - build and `start()` a `ScreenshotFeed` whose `on_stop` sets the title to
      `"Right Beam Line DAQ — SIMULATION STOPPED: " + reason`; return it.
    The HV interlock is computed by the real `HvInterlockLinkMixin` from the fake vacuum. Its
    line will read "OK - 0 kV permitted …" because commanded kV is read from real generators
    and there are none. Do not fake it: the interlock is the safety chokepoint.
16. `main(argv: list[str] | None = None) -> int`: `root = Path(tempfile.mkdtemp(prefix="rbl-screenshot-"))`;
    `isolate_environment(root)`; print `screenshot mode: all app files go to <root>`;
    `logging.basicConfig(level=logging.INFO)`; then import `make_application`, `MainWindow`;
    `app = make_application(sys.argv if argv is None else ["rbl-screenshot", *argv])`;
    `win = MainWindow()`; `feed = start_screenshot_mode(win)`; `win.showMaximized()`;
    `rc = app.exec()`; `feed.stop("window closed")` if `feed.active`; return `rc`.
    `if __name__ == "__main__": sys.exit(main())`.

## Guardrails

- Nothing under `src/` and nothing in `rbl.spec` may reference `screenshot_overview`.
- Tests must not call `main()`, `isolate_environment()` in-process, or the real
  `set_interlock_gauges` (it persists to the real config dir in a test process).
- No `_`-prefixed attribute access in either test file; `tools/private_access_ratchet.txt`
  must not change.
- Do not change the Overview's normal default arrangement.

## Acceptance criteria

All tests new, in `tests/test_screenshot_overview.py` unless noted.

- [ ] `test_importing_the_script_loads_no_rbl_module`: a subprocess (`sys.executable -c`,
  cwd = repo root) imports `scripts.screenshot_overview` and prints whether any `rbl` /
  `rbl.*` key is in `sys.modules`; output is `False`.
- [ ] `test_isolation_redirects_every_persistence_root`: a subprocess calls
  `isolate_environment(<tmp_path>)`, then imports `rbl.config.paths` and prints `CONFIG_DIR`
  and `LOG_ROOT`; both resolve under `tmp_path`.
- [ ] `test_isolation_refuses_after_rbl_is_imported`: in-process (rbl is already imported by
  the test session), `isolate_environment(tmp_path)` raises `RuntimeError` and
  `os.environ["HOME"]` is unchanged.
- [ ] `test_nothing_in_the_app_references_the_script`: no `*.py` under `src/` and not
  `rbl.spec` contains the text `screenshot_overview`.
- [ ] `test_scene_is_healthy_by_the_apps_own_rules` (pure): every readback `amp / 2` is below
  `PEAK_WARN_VOLTS`; `CHAMBER_TORR <= UI_GOOD_VACUUM_TORR`;
  `interlock_status(CHAMBER_TORR, max peak, pressure_known=True)[0] == "ok"`.
- [ ] `test_scene_round_trips_through_the_real_conversion`: a plain `Beamline()`; capture
  `logamps_changed`, `amps_changed`, `motors_changed`; `push_scene(...)`; each slit current is
  within 0.5 % of `SLIT_CURRENTS_A`, each `peak_kv` within 0.01 of its axis peak, each
  `pos_mm` within 0.01 mm of `SLIT_POSITIONS_MM`.
- [ ] `test_scene_reaches_the_overview_screen`: `OverviewTab(Beamline())`, `tab.show()`,
  `qapp.processEvents()`, `push_scene`, `tab.redraw()`; then every `tab.pills[k].styleSheet()`
  for `motors, logamps, amps, A, B` equals `theme.pill(True)` (the redraw sets it with no
  suffix);
  `tab.hv_phase["X"].text()` and `["Y"]` start with `"push-pull locked"`;
  `tab.lbl_timebase.text() == "A: INT  B: EXT"`; `tab.lbl_gaps.text()` contains `"X: "` and
  `"Y: "` followed by the sums of the two published `pos_mm` values formatted `.3f`.
- [ ] `test_feed_stops_when_real_data_arrives`: `ScreenshotFeed(Beamline(), build_scene(), on_stop=reasons.append)`;
  after `start()`, `active` is True and `reasons == []`; after
  `beamline.motors_changed.emit(MotorState(connected=True, zeroed=True, axes={}))`, `active`
  is False and `reasons == ["real motors_changed data arrived"]`.
- [ ] `test_feed_stops_when_an_instrument_connects`: same, with `beamline.labjack_connected.emit("470000000")`;
  reason `"a real instrument connected"`.
- [ ] `test_feed_survives_its_own_pushes`: after `start()` and three `push()` calls, `active` is True.
- [ ] `test_screenshot_mode_arranges_titles_and_unhooks_connect_all`: `MainWindow()`,
  `monkeypatch.setattr(win.beamline, "set_interlock_gauges", recorded.append)`;
  `feed = start_screenshot_mode(win)`; title ends with `TITLE_SUFFIX`;
  `recorded == [{CHAMBER_GAUGE}]`; `win.overview_tab.panel_area.column_names() == SCREENSHOT_LAYOUT`;
  `win.overview_tab.btn_connect_all.click()` then `processEvents()` leaves the button text
  `"Connect All"` (wired, it would read `"Connecting…"`); `feed.stop("test")` at the end.
- [ ] `test_overview_names_every_panel`: `MainWindow()`; the sorted flattened
  `panel_area.column_names()` equals the sorted eight names in step 5.
- [ ] (`tests/test_drag_panel.py`) `test_arrange_reorders_panels_by_name`: a `PanelArea` with
  `DragPanel(QLabel(), name=n)` for `a, b, c` added in columns 0, 0, 1;
  `arrange([["c"], ["a", "b"]])` → `column_names() == [["c"], ["a", "b"]]`.
- [ ] (`tests/test_drag_panel.py`) `test_arrange_rejects_a_layout_that_loses_or_repeats_a_panel`:
  missing `b`, duplicated `a`, and unknown `z` each raise `ValueError`, and `column_names()`
  is unchanged after each.
- [ ] By hand (state it in the PR, it cannot run in CI): `python scripts/screenshot_overview.py`
  opens maximized on the Overview, three columns as `SCREENSHOT_LAYOUT`, every subsystem pill
  green, both push-pull captions "locked", and `python -m rbl.main` still opens on today's
  5-column layout with no data.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile

## Out of scope

- A better **default** Overview layout, saving/restoring layouts, or a layout picker.
- Simulated data on any tab other than the Overview (other tabs may show partial data; ignore).
- Scope / beam FWHM, Faraday cup readings, session recorder state.
- Faking the HV interlock's commanded kV.
- Any flag, menu, env var or entry point in `src/` or `rbl.spec` that reaches this mode.
- Disabling the per-tab Connect buttons (the feed's stop rule covers real data arriving).

## Comments
