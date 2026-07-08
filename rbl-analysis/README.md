# RBL-Analysis

Analysis-only spinoff of the Right Beam Line (RBL) desktop tool. This app is
**hardware-free**: it contains the dose-map / trajectory / metrics / optimizer
simulation stack and the voltage & magnet calculators, but none of the DAQ
code (stepper motors, beam current, function generators). Those stay in the
original `rbl` repository.

## Run

```bash
python -m rbla.main
```

Optional CLI:

```bash
python -m rbla.main --validate          # run the physics/scan validation suite
python -m rbla.main --config config.yaml # headless pipeline (dose_map.csv, metrics.json, trajectory.gif)
```

## Install

```bash
pip install -r requirements.txt
```

The requirements deliberately exclude every hardware dependency
(`pyvisa`, `pyusb`, `libusb-package`, `labjack-ljm`) — that absence is the
whole point of the split.

## Test

```bash
QT_QPA_PLATFORM=offscreen python -m pytest -q
```
