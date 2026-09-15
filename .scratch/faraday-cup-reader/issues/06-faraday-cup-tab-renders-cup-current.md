# 06: Faraday Cup tab renders cup current

**What to build:** A Faraday Cup tab showing the live cup current, with its own connect
panel so the cup can be brought up without connecting the whole beamline. Inserted after
Slit Currents.

This is the first moment the application displays two different current quantities at
once, which is the situation `CONTEXT.md`'s cross-tab agreement invariant exists to
guard. ADR 0001 records that nothing in the suite currently tests that invariant.

**Blocked by:** 03, 05

**Status:** done

- [x] A tab titled "Faraday Cup" appears directly after "Slit Currents"
- [x] It renders the cup current snapshot, auto-scaling across nanoamps to milliamps
- [x] It converts nothing — it renders what the snapshot carries
- [x] It owns no driver and no instrument handle
- [x] An over-range reading is shown as such, not as a number
- [x] Its own connect panel connects and disconnects the picoammeter alone
- [x] Connection state is presented the same way other instruments present theirs
- [x] With nothing connected the tab says so, and shows neither a stale value nor zero
- [x] The tab is usable with no hardware present at all
- [x] A test drives the cup feed helper and asserts on what the tab renders
- [x] A cross-tab agreement test asserts that slit current and cup current are rendered
      as distinct, differently-labelled quantities, and that the cup reading is not
      derived from or conflated with the log-amp path
- [x] Tests assert on rendered output, not on private widget attributes

## Comments

### 2026-09-14
- Implemented `FaradayCupTab` in `src/rbl/gui/faraday_cup_tab.py`:
  - Dedicated connection box with VISA resource input, Connect/Disconnect button, `StatusPill`, and ident readback.
  - Live Cup Current readout display with auto-scaling across nA, µA, and mA using `format_current()`.
  - Explicit "OVER-RANGE" warning display when status word bit 6 or over-range sentinel is active (never rendered as a numeric value).
  - Explicit placeholder ("—") when disconnected or unavailable, preventing zero or stale values.
- Integrated `FaradayCupTab` in `src/rbl/gui/app.py`:
  - Added to `TAB_DECLARATIONS` directly after "Slit Currents".
  - Connected `Beamline.cup_changed`, `cup_error`, `cup_connected`, `cup_disconnected_evt` signals in `MainWindow.__init__`.
  - Wired `faraday_cup_tab.connect_if_needed` into `_connect_all_steps()`.
- Updated test suite:
  - Added `tests/test_faraday_cup_tab.py` with comprehensive unit and integration tests covering initial disconnected state, connect lifecycle, auto-scaling readouts, over-range text rendering, unavailable sentinel rendering, disconnection reset, and cross-tab agreement between Slit Currents and Faraday Cup tabs.
  - Updated `test_gui_hardware.py` expected tab ordering.
- Full local gate passed: ruff, check_tests_first, type_gate, and pytest (1695 passed).
