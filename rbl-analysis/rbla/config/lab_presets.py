"""
lab_presets.py
Named frequency presets for the EEL5000 / XY Steerer raster scan system.

Each entry maps a human-readable label to f1_hz (fast / X axis) and
f2_hz (slow / Y axis).  These are display-only helpers -- they do not
modify the compute pipeline.
"""

FREQUENCY_PRESETS = {
    "Michigan  (f1=2061 Hz, f2=255 Hz)":       {"f1_hz": 2061.0, "f2_hz":  255.0},
    "IBL (f1=517 Hz, f2=64 Hz)":       {"f1_hz": 517.0, "f2_hz":  64.0},
    "Thomas Jefferson (f1=24.96 kHz, f2=25.920 kHz)":     {"f1_hz": 25920.0, "f2_hz":  24960.0},
    "Oxford (f1=10004.5 Hz, f2=1000 Hz)":       {"f1_hz": 1004.5, "f2_hz":  1000},
}
