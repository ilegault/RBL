# DAQ App Cleanup Plan (source `rbl` repo) — deferred, NOT executed

This is a **written plan only**, per Phase 8 step 2 of the split spec. Nothing
in the source `rbl` repository has been changed. The analysis tabs now exist in
two places (the source `rbl/gui/app.py` and the new `rbla/gui/app.py`); long
term the DAQ app should drop the analysis half. That deletion is risky and
belongs in its own spec with its own self-tests — it is intentionally decoupled
from the additive work in this split.

## What would be removed from `rbl/gui/app.py` to make the DAQ app hardware-only

Analysis-only classes/functions defined inline in `rbl/gui/app.py`:

- `ComputeWorker` — analysis compute thread (dose/trajectory/metrics).
- `PlotTab` — reusable matplotlib tab used only by the analysis plot tabs.
- `_dbl`, `_int` — spinbox helpers (shared; keep if hardware tabs also use them,
  otherwise remove).
- `ParamPanel` — the analysis parameter panel.
- `VoltageCalcTab` — voltage / deflection calculator.
- `MagnetCalcTab` — magnet field calculator (only exists in the new app; N/A
  to the source repo unless it is ever back-ported).

In `MainWindow.__init__`, the analysis page construction would be removed:

- the analysis `central` page and its layout,
- `self.params_panel = ParamPanel()`,
- the inner `self.tabs` QTabWidget and every `addTab(...)` for:
  `Dose Map (2D)`, `Dose Surface (3D)`, `Velocity Profile`,
  `Dwell Distribution`, `Trajectory`, `Waveform Comparison`,
  `Voltage Calculator`,
- `self.run_btn` / `self.progress` / `self.status_lbl` wiring,
- the auto-run `QTimer.singleShot(200, self.run)`,
- the `run`, `_on_result`, `_on_error`, `_make_trajectory_fig` methods.

The outer tab bar would collapse to the three hardware pages
(`Stepper Motors`, `Beam Current`, `Function Generators`) — the inverse of the
Phase 2 edit made in the analysis app.

Analysis-only imports that would then be unused and removable from the top of
`rbl/gui/app.py`:

- `from rbl.config.defaults import DEFAULTS`
- `from rbl.scan.patterns import get_realistic_trajectory`
- `from rbl.scan.dose import compute_dose`
- `from rbl.scan.metrics import compute_all_metrics, _aperture_mask_from_edges`
- `from viz import ...` (all plot_* helpers)
- `from rbl.physics.deflection_physics import ...`
- `from rbl.config.lab_presets import FREQUENCY_PRESETS`

## What STAYS in the DAQ app

- `HardwareView` and its motors/current splitter logic.
- `motor_tab.py`, `current_tab.py`, `funcgen_tab.py` and the `hardware/`
  package (galil, labjack, current_monitor, funcgen drivers, slit_config).
- The hardware teardown in `closeEvent` (motor abort, current shutdown,
  funcgen close).
- The `_on_outer_tab_clicked` split/navigation logic.

## Why this is deferred

Deleting the analysis tabs from the shipping DAQ app is a behavior change for
users who currently rely on that single combined window. It needs its own spec
covering: migration messaging, whether any analysis capability must remain in
the DAQ app, and regression tests for the hardware tabs after the analysis code
is pulled out. Keeping it separate keeps the risky deletion decoupled from the
additive split delivered here.
