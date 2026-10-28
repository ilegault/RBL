"""
session_regulation_watch.py
Feeds the regulation detector from the raw stream while a session runs, and
turns what it confirms into Overview warnings. Commands nothing.

WHY THIS EXISTS
---------------
`RegulationMonitor` judges one window at a time but somebody has to build that
window's numbers. Calibration and characterization do it themselves because
they own the drive; a session does not drive anything through a runner, so the
operator's setpoints are the only record of what the plates were asked to do.
This object compares them with what the monitors say, plate by plate, for the
whole session (ADR 0006: the amplifiers protect themselves, the application is
the witness - so the paired `RegulationResponder` runs in `observe` mode).

UNITS AND CONVENTIONS
---------------------
* Commanded kV per plate is `amp_vpp / 2` (1 V at the generator is 1 kV at the
  plate), or the offset magnitude for a DC setpoint (frequency 0).
* Measured kV is the lock-in fundamental (`ac_metrics.fundamental`) of the
  voltage monitor at the setpoint frequency, or the window mean for DC. A
  triangle's fundamental is 81 % of its peak, a square's 127 %; both sit far
  from the 50 % ceiling the detector uses, so no per-shape correction is made.
* Current is the peak magnitude of the current monitor in the window, against
  the 20 mA continuous rating (`AMP_CONTINUOUS_RATING_MA`).
* Raw volts are converted with the monitor scale factors in hardware_config,
  as `SpikeRecorder` does with the same raw window.

A plate is judged only while its channel is on with a non-zero amplitude, and
the monitor's debounce is cleared when a watch starts so a fault from an earlier
session cannot re-fire.
"""
import logging

import numpy as np
from PySide6.QtCore import QObject

from rbl.config import hardware_config
from rbl.config.hardware_config import (
    AMP_CHANNEL_MAP,
    AMP_CONTINUOUS_RATING_MA,
    VOLTAGE_MONITOR_KV_PER_VOLT,
)
from rbl.hardware.ac_metrics import fundamental
from rbl.hardware.funcgen_safety import CHANNEL_ROLE

log = logging.getLogger(__name__)


class SessionRegulationWatch(QObject):
    def __init__(self, setpoints, monitor, parent=None):
        super().__init__(parent)
        self._setpoints = setpoints
        self._monitor = monitor
        self._running = False

    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        self._monitor.reset()
        self._running = True

    def stop(self) -> None:
        self._running = False

    def operating_conditions(self, label: str) -> dict:
        """What the plate was asked to do, for the trip-history record."""
        key = next((k for k, p in CHANNEL_ROLE.items() if p == label), None)
        if key is None:
            return {}
        params = self._setpoints.get(key)
        return {"commanded_kv": self._commanded_kv(params),
                "frequency_hz": params.freq_hz, "waveform": params.shape}

    @staticmethod
    def _commanded_kv(params) -> float:
        if params.freq_hz <= 0:
            return abs(params.offset_v)
        return params.amp_vpp / 2.0

    def on_window(self, payload: dict) -> None:
        """Wire to Beamline.raw_window_ready."""
        if not self._running:
            return
        dt = payload.get("sample_period")
        if not dt:
            return
        channels = payload.get("channels", {})
        for key, plate in CHANNEL_ROLE.items():
            params = self._setpoints.get(key)
            if not (params.output_on and params.amp_vpp != 0):
                continue
            amps = AMP_CHANNEL_MAP[plate]
            i_entry = channels.get(amps["current"])
            v_entry = channels.get(amps["voltage"])
            if not (i_entry and v_entry and "waveform" in i_entry
                    and "waveform" in v_entry):
                continue
            v_kv = np.asarray(v_entry["waveform"], dtype=float) \
                * VOLTAGE_MONITOR_KV_PER_VOLT
            i_ma = np.asarray(i_entry["waveform"], dtype=float) \
                * hardware_config.CURRENT_MONITOR_MA_PER_VOLT
            if v_kv.size == 0 or i_ma.size == 0:
                continue
            if params.freq_hz <= 0:
                measured = abs(float(np.mean(v_kv)))
            else:
                measured, _phase = fundamental(v_kv, 1.0 / float(dt), params.freq_hz)
                if measured != measured:     # too few whole cycles to be honest
                    continue
            self._monitor.evaluate(
                plate, measured, self._commanded_kv(params),
                float(np.max(np.abs(i_ma))), AMP_CONTINUOUS_RATING_MA)
