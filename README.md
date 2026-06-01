# RBL — Ion-Beam Raster Scan Analysis Tool

Desktop application for planning, simulating, and evaluating ion-beam raster scan parameters at accelerator facilities (UW-IBL, MIBL, and compatible sites).

---

## Overview

RBL computes dose uniformity metrics for a user-defined raster scan trajectory and displays results across eight analysis views. It also provides live hardware control for a Galil DMC-4103 stepper motor controller and a LabJack T7 beam-current monitor, both running as independent background threads — data continues accumulating regardless of which tab is active.

---

## Features

| Category | Capability |
|---|---|
| Scan patterns | Classic raster, alternating axes, Lissajous, spiral, sinusoidal, wobble |
| Dose metrics | Flatness (ASTM E521), RMS deviation, pinch, dwell distribution, triangularity |
| Physics models | EEL5000 amplifier bandwidth filter + slew-rate clamp, FDRT steady-state flag, diffusion length |
| Optimizer | Grid search + differential evolution over (f₁, f₂, X/Y overscan) |
| Voltage calculator | Plate voltage / function-gen amplitude for any ion species, energy, and deflection |
| Hardware — motors | Galil DMC-4103 over TCP: jog, absolute move, E-STOP, soft-limit readout |
| Hardware — current | LabJack T7: 4-channel log-amp current monitor with live rolling plot |
| Split view | Side-by-side tab display — click **[ Split ]** to view two plots simultaneously |

---

## Hardware Requirements

| Device | Interface | Driver |
|---|---|---|
| Galil DMC-4103 motion controller | TCP/IP (port 23) | Built-in ASCII socket protocol |
| LabJack T7 analog input | USB or Ethernet | `labjack-ljm` Python package + LJM system library |

Both devices are optional — the Analysis tab and all simulation functions work without any hardware connected.

---

## Installation

```bash
# Clone the repository
git clone https://github.com/ilegault/rbl.git
cd rbl

# Install dependencies
pip install -r requirements.txt
```

**Note:** `requirements.txt` lists `PyQt5` for compatibility notes, but the application uses `PySide6`. Install both if needed:

```bash
pip install PySide6
```

For LabJack T7 support, install the LJM system library from [labjack.com](https://labjack.com/pages/support) then:

```bash
pip install labjack-ljm
```

---

## Running the Application

```bash
# From the repository root
python rbl/main.py

# Headless validation run (no display required)
python rbl/main.py --validate

# Headless config dump
python rbl/main.py --config
```

Or run the GUI directly:

```bash
cd rbl
python gui/app.py
```

---

## Application Layout

### Outer Tabs

| Tab | Description |
|---|---|
| **Analysis** | Simulation and dose uniformity analysis — parameter panel on the left, results on the right |
| **Stepper Motors** | Galil DMC-4103 control: per-axis jog/move/stop, E-STOP, command console |
| **Beam Current** | LabJack T7 live current readout, beam-centering indicator, 60-second rolling plot |

### Analysis Sub-Tabs

| Tab | Description |
|---|---|
| Dose Map (2D) | Colour-mapped dose heatmap with aperture overlay |
| Dose Surface (3D) | 3D surface plot of the dose distribution |
| Velocity Profile | Instantaneous beam velocity vs. time |
| Dwell Distribution | Histogram of time-per-pixel inside the aperture |
| Trajectory | Scatter plot of the beam path, coloured by time |
| Metrics | All computed metrics in a table with FDRT and FWHM status badges |
| Optimizer | Grid search heatmap + differential evolution solver |
| Voltage Calculator | Required plate voltage for any species / energy / deflection |

### Split View

Click **[ Split ]** in the top bar to open a second tab panel side by side. Both panels update whenever a new computation completes. Switch tabs in the right panel — only the visible tab renders (on-demand) to avoid unnecessary computation.

---

## Scan Patterns

| Pattern | Description |
|---|---|
| `classic` | Triangle on X (fast axis), ramp on Y (slow axis). MIBL canonical. |
| `alt_axes` | Swaps which axis is fast on every Y frame. |
| `lissajous` | Sine on both axes with optional phase offset φ. |
| `spiral` | Radius grows linearly, angle spins at f₂ (f₁ is unused). |
| `sinusoidal` | Sine on fast axis, ramp on slow axis. |
| `wobble` | Sine on both axes — small defocus wobble pattern. |

---

## Physics Models

### Amplifier (EEL5000.20.100)

Signal chain: DG1000Z (±10 V) → EEL5000 (×1000 gain, ±5 kV) → NEC ES5 steerer plates.

Simulation pipeline per axis:
1. Convert mm → kV → V
2. First-order low-pass FFT filter at the configured −3 dB bandwidth
3. Slew-rate clamp at the configured V/µs limit
4. Convert V → kV → mm

Enable/disable via the **Simulate amplifier** checkbox. When disabled, the ideal trajectory is used directly.

### FDRT Steady-State

A pixel is in steady state if the beam revisit rate (slowest axis frequency) exceeds both:
- The empirical FDRT floor (Gigax et al. 2015, default 500 Hz)
- The inverse of the defect recombination time τ_recomb

### Dose Metrics

- **Flatness**: (max − min) / (max + min) × 100 %. Target ≤ 10 % (ASTM E521).
- **Pinch**: edge-to-centre ratio along a horizontal aperture slice.
- **Triangularity**: Bhattacharyya spectral overlap between the actual X waveform and an ideal triangle at f₁.
- **Slew margin**: headroom between the amplifier's rated SR and the required SR. Negative = slew-limited.

---

## Configuration

Default parameters are in `rbl/config/defaults.py`. Lab-specific frequency presets (Michigan, IBL, Thomas Jefferson, Oxford) are in `rbl/config/lab_presets.py`. Select a preset from the **Lab preset** dropdown — it copies f₁/f₂ directly to the spin-boxes.

Hardware configuration (axis letters, jaw names, step/mm ratios, log-amp models) is in `rbl/hardware/slit_config.py`.

---

## Testing

```bash
# Run the full test suite
python -m pytest

# Run with verbose output
python -m pytest -v

# Run a specific module
python -m pytest tests/test_scan.py -v
```

### Test Coverage

| Module | Tests |
|---|---|
| `test_current_monitor.py` | `voltage_to_current`, `format_current`, `beam_centering`, `RollingBuffer` (thread safety) |
| `test_physics.py` | Low-pass filter, slew limiter, full amplifier pipeline, deflection voltage calculator |
| `test_scan.py` | All 6 scan patterns, dose computation, trajectory density, all metric functions, `compute_all_metrics` integration |
| `test_config.py` | DEFAULTS completeness and validity, frequency presets |
| `test_hardware.py` | `GalilController` lifecycle and mocked command parsing, `LabJackT7` lifecycle, `slit_config` round-trips |
| `test_tab_persistence.py` | Galil and LabJack poll threads continue accumulating data when their tab is not active; `RollingBuffer` concurrent read/write safety |

---

## Background Thread Behaviour

Both hardware poll threads (`GalilPollWorker`, `LabJackPollWorker`) are Qt worker threads that run independently of which tab is visible. They emit signals that update the UI — but the signal queue drains regardless of tab visibility, and the `RollingBuffer` behind the current-monitor plot keeps filling at 10 Hz even when the Beam Current tab is hidden. The `test_tab_persistence.py` suite verifies this explicitly.

---

## Repository Structure

```
RBL/
├── rbl/
│   ├── main.py                # CLI entry point (--validate, --config, or GUI)
│   ├── config/
│   │   ├── defaults.py        # Global parameter defaults
│   │   ├── lab_presets.py     # Facility frequency presets
│   │   └── validation.py      # 8 offline validation checks
│   ├── gui/
│   │   ├── app.py             # Main PySide6 window + split-view
│   │   ├── motor_tab.py       # Galil stepper motor tab
│   │   ├── current_tab.py     # LabJack beam current tab
│   │   └── viz.py             # Matplotlib figure generators
│   ├── hardware/
│   │   ├── galil_driver.py    # TCP socket interface to Galil DMC-4103
│   │   ├── labjack_driver.py  # Wrapper for labjack-ljm
│   │   ├── current_monitor.py # Log-amp voltage→current, RollingBuffer
│   │   └── slit_config.py     # Axis/jaw/channel configuration
│   ├── physics/
│   │   ├── amplifier.py       # EEL5000 bandwidth + slew model
│   │   ├── deflection_physics.py  # Voltage-to-deflection formula
│   │   ├── beam.py            # Beam constants
│   │   └── physics_refs.py    # Literature references
│   └── scan/
│       ├── patterns.py        # 6 trajectory generators
│       ├── dose.py            # 2D Gaussian convolution dose map
│       ├── metrics.py         # Flatness, FDRT, pinch, triangularity, …
│       └── optimizer.py       # Grid search + differential evolution
├── tests/
│   ├── conftest.py
│   ├── test_config.py
│   ├── test_current_monitor.py
│   ├── test_hardware.py
│   ├── test_physics.py
│   ├── test_scan.py
│   └── test_tab_persistence.py
├── pytest.ini
└── requirements.txt
```

---

## License

See `LICENSE` in the repository root.
