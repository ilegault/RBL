"""
spike_recorder.py
Watches every driven amplifier's current monitor for current spikes, keeps the
evidence, and commands nothing.

WHY THIS EXISTS
---------------
The operator has to explain, after a 12-hour irradiation, exactly how close each
amplifier came to its limits and what happened when one misbehaved. The
amplifiers protect themselves (LIMIT, TRIP); the application is the WITNESS
(docs/adr/0006-amplifier-faults-are-observed-not-stopped-during-a-session.md).
So this service only observes: it writes a row per spike, saves the raw monitor
waveform around it, and tells whoever is listening. It never turns an output
off, changes a setpoint, calls a generator or an amplifier drive, or opens a
dialog - a tool still under development must not be able to end an irradiation.
tests/test_spike_recorder.py replaces every such method with one that fails the
test and runs a recording with spikes in it.

HOW A SPIKE IS JUDGED
---------------------
Against the REFERENCE CURRENT of the plate's current operating point: the first
REFERENCE_S seconds after the plate starts being driven (or after any setpoint
change on its channel) are used only to capture the reference and the noise
(hardware/spike_detector.py), and no spike is recorded for that plate until the
reference exists. A small raster draws 2 mA and a big one 15 mA; a fixed alarm
level cannot serve both.

WHAT IT KEEPS
-------------
* `spikes.csv` in the output folder: one row per spike, flushed immediately (a
  run that dies at hour 11 must leave 11 hours of rows).
* `spikes/<plate>_<stamp>.csv`: BEFORE_S before and AFTER_S after a spike, both
  monitors. A spike that starts inside an open file's after-window extends that
  file rather than starting another, because the recovery question ("did two
  bursts come less than 100 ms apart?") needs them on one trace. A file is
  capped at MAX_EVENT_S so a storm of spikes cannot grow memory for hours.
* `spikes/<plate>_pre_event_<stamp>.csv` on `save_pre_event`: the last 10 s,
  for studying what leads up to an amplifier shutting itself off.

GAP BETWEEN SPIKES
------------------
`gap_to_previous_s` runs from the END of the previous spike to the START of this
one: the manual's recovery period (100 ms at no more than 10 mA) is measured
from the end of a burst.

TIME
----
Spike times are stream time (sample count x sample interval since `start`) so
they stay aligned with the waveforms; the wall-clock time in `time_iso` is the
wall time at `start` plus that offset, with the wall clock injected (`now_fn`).
Windows must arrive back to back; a window that lacks a plate's monitors is
skipped for that plate.

UNITS
-----
Windows arrive as raw monitor volts on Beamline.raw_window_ready (like the
calibration runner and the load characterizer, which also consume the raw
stream), converted here with the monitor scale factors in hardware_config.
"""
import csv
import logging
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, Signal

from rbl.config import amplifier_assignments
from rbl.config.hardware_config import (
    AMP_CHANNEL_MAP,
    CURRENT_MONITOR_MA_PER_VOLT,
    VOLTAGE_MONITOR_KV_PER_VOLT,
)
from rbl.hardware.funcgen_safety import CHANNEL_ROLE
from rbl.hardware.spike_detector import (
    SpikeDetector,
    capture_reference,
    spike_threshold,
)
from rbl.services.log_rollover import unused_path

log = logging.getLogger(__name__)

REFERENCE_S = 60.0     # reference + noise captured over this long per operating point
RING_S = 10.0          # rolling history of both monitors per driven plate
BEFORE_S = 2.0         # waveform saved before a spike...
AFTER_S = 2.0          # ...and after it
MAX_EVENT_S = 60.0     # a merged waveform file never spans more than this

SPIKE_COLUMNS = [
    "time_iso", "plate_position", "amplifier_serial", "duration_s", "peak_ma",
    "peak_is_lower_bound", "charge_uc", "gap_to_previous_s",
    "sample_interval_s", "reference_ma", "threshold_ma",
]
WAVE_COLUMNS = ["t_s", "voltage_kv", "current_ma"]
NO_SERIAL = "not recorded"
_STAMP = "%Y%m%dT%H%M%S_%f"


class _Plate:
    """Everything the recorder knows about one driven plate."""

    def __init__(self):
        self.ref_chunks: list = []
        self.ref_samples = 0
        self.reference = None
        self.detector = None
        self.ring: deque = deque()      # (t0_s, volts_kv array, current_ma array)
        self.last_end_s = None          # end of the previous spike on this plate
        self.pending = None             # waveform file waiting for its after-window

    def restart_reference(self):
        self.ref_chunks = []
        self.ref_samples = 0
        self.reference = None
        self.detector = None


class SpikeRecorder(QObject):
    """Records current spikes from raw stream windows. Commands nothing."""

    spike_recorded = Signal(dict)
    reference_captured = Signal(str, dict)

    def __init__(self, setpoints, now_fn=datetime.now, parent=None):
        super().__init__(parent)
        self._setpoints = setpoints
        self._now = now_fn
        self._running = False
        self._dir = None
        self._csv_file = None
        self._csv = None
        self._plates: dict = {}
        self._forced: frozenset = frozenset()
        self._t = 0.0
        self._dt = None
        self._wall0 = None
        # A setpoint change is a new operating point: its reference is stale.
        # Connecting here (not only in MainWindow) means a recorder built on its
        # own still re-captures; a second connection only repeats a reset.
        if setpoints is not None:
            setpoints.changed.connect(self.on_setpoint_changed)

    # ---- lifecycle ----------------------------------------------------------

    def is_running(self) -> bool:
        return self._running

    def start(self, output_dir, driven_plates=None) -> None:
        """Begin recording into `output_dir` (created if needed).

        `driven_plates` names plates to treat as driven whatever the setpoint
        model says. A drift pass drives the amplifiers straight through the
        calibration runner, which does not go through FuncGenSetpoints, so the
        model still reads "output off" and the recorder would watch nothing;
        the pass says which plates it is driving instead.
        """
        if self._running:
            self.stop()
        self._dir = Path(output_dir)
        (self._dir / "spikes").mkdir(parents=True, exist_ok=True)
        self._csv_file = open(self._dir / "spikes.csv", "w",
                              newline="", encoding="utf-8")
        self._csv = csv.DictWriter(self._csv_file, fieldnames=SPIKE_COLUMNS,
                                   lineterminator="\n")
        self._csv.writeheader()
        self._csv_file.flush()
        self._plates = {}
        self._forced = frozenset(driven_plates or ())
        self._t = 0.0
        self._dt = None
        self._wall0 = self._now()
        self._running = True

    def stop(self) -> None:
        """Finish: write any waveform still waiting for its after-window."""
        if not self._running:
            return
        for plate, st in list(self._plates.items()):
            self._write_pending(plate, st)
        self._plates = {}
        if self._csv_file is not None:
            self._csv_file.close()
        self._csv_file = None
        self._csv = None
        self._running = False

    # ---- inputs -------------------------------------------------------------

    def on_setpoint_changed(self, key: str, params=None) -> None:
        """A channel's setpoint moved: its reference no longer applies."""
        plate = CHANNEL_ROLE.get(key)
        st = self._plates.get(plate)
        if st is not None:
            st.restart_reference()

    def on_window(self, payload: dict) -> None:
        """Wire to Beamline.raw_window_ready: the raw per-AIN stream window."""
        if not self._running:
            return
        dt = payload.get("sample_period") or self._dt
        if not dt:
            return
        self._dt = float(dt)
        channels = payload.get("channels", {})
        n = int(payload.get("window_samples") or 0)
        t0 = self._t

        for key, plate in CHANNEL_ROLE.items():
            params = self._setpoints.get(key)
            if plate not in self._forced and not (params.output_on and params.amp_vpp != 0):
                st = self._plates.pop(plate, None)
                if st is not None:
                    self._write_pending(plate, st)
                continue
            amps = AMP_CHANNEL_MAP[plate]
            i_entry = channels.get(amps["current"])
            v_entry = channels.get(amps["voltage"])
            if not (i_entry and v_entry and "waveform" in i_entry
                    and "waveform" in v_entry):
                continue
            i_ma = np.asarray(i_entry["waveform"], dtype=float) \
                * CURRENT_MONITOR_MA_PER_VOLT
            v_kv = np.asarray(v_entry["waveform"], dtype=float) \
                * VOLTAGE_MONITOR_KV_PER_VOLT
            n = n or int(i_ma.size)
            self._on_plate_window(plate, self._plates.setdefault(plate, _Plate()),
                                  t0, v_kv, i_ma)

        self._t = t0 + n * self._dt

    def save_pre_event(self, plate: str, seconds: float = 10.0) -> None:
        """Save the last `seconds` of both monitors for `plate`."""
        st = self._plates.get(plate)
        if not self._running or st is None or not st.ring:
            return
        t_end = st.ring[-1][0] + st.ring[-1][2].size * self._dt
        rows = self._slice(st, t_end - seconds, t_end)
        if not rows:
            return
        stamp = self._now().strftime(_STAMP)
        path = unused_path(self._dir / "spikes", f"{plate}_pre_event_{stamp}", ".csv")
        self._write_wave(path, rows)

    # ---- per-plate processing ----------------------------------------------

    def _on_plate_window(self, plate, st, t0, v_kv, i_ma):
        dt = self._dt
        te = t0 + i_ma.size * dt
        st.ring.append((t0, v_kv, i_ma))

        if st.reference is None:
            st.ref_chunks.append(i_ma)
            st.ref_samples += i_ma.size
            if st.ref_samples * dt >= REFERENCE_S:
                ref = capture_reference(np.concatenate(st.ref_chunks), dt)
                st.reference = ref
                st.detector = SpikeDetector(ref, dt)
                st.ref_chunks = []
                self.reference_captured.emit(plate, {
                    "current_ma": ref.current_ma,
                    "noise_ma": ref.noise_ma,
                    "threshold_ma": spike_threshold(ref),
                })
        else:
            for spike in st.detector.feed(i_ma, t0):
                self._record(plate, st, spike)

        if st.pending is not None and te >= st.pending["t_end"]:
            self._write_pending(plate, st)
        self._trim(st, te)

    def _record(self, plate, st, spike):
        end = spike.start_s + spike.duration_s
        wall = self._wall0 + timedelta(seconds=spike.start_s)
        gap = None if st.last_end_s is None else spike.start_s - st.last_end_s
        st.last_end_s = end
        assignment = amplifier_assignments.current_assignment(wall)
        serial = (assignment or {}).get(plate, NO_SERIAL)
        row = {
            "time_iso": wall.isoformat(),
            "plate_position": plate,
            "amplifier_serial": serial,
            "duration_s": spike.duration_s,
            "peak_ma": spike.peak_ma,
            "peak_is_lower_bound": spike.peak_is_lower_bound,
            "charge_uc": spike.charge_uc,
            "gap_to_previous_s": "" if gap is None else gap,
            "sample_interval_s": self._dt,
            "reference_ma": st.reference.current_ma,
            "threshold_ma": st.detector.threshold_ma,
        }
        self._csv.writerow(row)
        self._csv_file.flush()

        p = st.pending
        if (p is not None and spike.start_s <= p["t_end"]
                and end + AFTER_S - p["t_begin"] <= MAX_EVENT_S):
            p["t_end"] = end + AFTER_S
        else:
            if p is not None:
                self._write_pending(plate, st)
            st.pending = {"t_begin": spike.start_s - BEFORE_S,
                          "t_end": end + AFTER_S, "wall": wall}
        self.spike_recorded.emit(dict(row))

    # ---- waveform files -----------------------------------------------------

    def _trim(self, st, t_now):
        cutoff = t_now - RING_S
        if st.pending is not None:
            cutoff = min(cutoff, st.pending["t_begin"])
        while st.ring and st.ring[0][0] + st.ring[0][2].size * self._dt <= cutoff:
            st.ring.popleft()

    def _slice(self, st, t_begin, t_end):
        dt = self._dt
        rows = []
        for t0, v, i in st.ring:
            t = t0 + np.arange(i.size) * dt
            keep = (t >= t_begin - 1e-12) & (t <= t_end + 1e-12)
            if keep.any():
                rows.append((t[keep], v[keep], i[keep]))
        return rows

    def _write_pending(self, plate, st):
        p = st.pending
        st.pending = None
        if p is None:
            return
        rows = self._slice(st, p["t_begin"], p["t_end"])
        if not rows:
            return
        path = unused_path(self._dir / "spikes",
                           f"{plate}_{p['wall'].strftime(_STAMP)}", ".csv")
        self._write_wave(path, rows)

    @staticmethod
    def _write_wave(path, parts) -> None:
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(WAVE_COLUMNS)
            for t, v, i in parts:
                for row in zip(t, v, i):
                    w.writerow([f"{x:.9g}" for x in row])
