"""
screenshot_overview.py
Open the real app with the Overview filled by a canned mid-irradiation scene.

WHY THIS EXISTS
---------------
Clean screenshots of a working Overview (talks, write-ups) are needed without a
beamline attached. This script fakes the instruments' *outputs* (a motor poll, a
LabJack window, a function-generator readback, a vacuum snapshot), pushes them
through Beamline's public ingest surface, and lets the real conversion and the
real Overview render them. Nothing is drawn by hand.

Design constraints, each of which is a requirement:
  - Dev-only. Nothing in src/ imports this file and PyInstaller never bundles
    it, so normal operation cannot reach this mode (no flag, menu or env var).
  - Isolated. Every persistence path (config dir, log root) is redirected into a
    fresh temp folder before any `rbl` module loads, because rbl.config.paths
    freezes Path.home() at import time. That is why this file imports nothing
    from `rbl` at module level.
  - Self-stopping. Once any real instrument reports, re-pushing fake positions
    over real ones on a live control panel would be a hazard, so the feed
    stops itself (ScreenshotFeed).
  - The HV interlock line is NOT faked. The real HvInterlockLinkMixin computes
    it from the fake vacuum snapshot; with no real generators the commanded kV
    is 0, so it reads "OK - 0 kV permitted ...". The interlock is the safety
    chokepoint and a screenshot tool must not be able to paint over it.

Run:  python scripts/screenshot_overview.py
"""
from __future__ import annotations

import dataclasses
import logging
import math
import os
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PySide6.QtCore import QTimer

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

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


def isolate_environment(root: Path) -> None:
    """Point every persistence root at *root*; must run before any rbl import."""
    if any(m == "rbl" or m.startswith("rbl.") for m in sys.modules):
        raise RuntimeError("isolate_environment must run before any rbl import")
    os.environ["HOME"] = str(root)
    os.environ["USERPROFILE"] = str(root)
    os.environ["RBL_LOG_ROOT"] = str(root / "Desktop" / "RBL_log")


@dataclass(frozen=True)
class Scene:
    motor_poll: dict
    zeroed: bool
    labjack_payload: dict
    funcgen_connected: dict
    funcgen_timebase: dict
    funcgen_readback: dict
    axis_setpoints: dict
    vacuum: object


def build_scene() -> Scene:
    """The canned scene. Pure: no Qt, and no clock except `vacuum.timestamp`."""
    from rbl.config import hardware_config as SC
    from rbl.hardware.vgc083_driver import VgcReading
    from rbl.state.setpoints import AXIS_CHANNELS
    from rbl.state.snapshots import VacuumState
    from tests.payloads import window_payload

    motor_poll = {
        letter: {
            "pos": SC.mm_to_counts(letter, SLIT_POSITIONS_MM[slit]),
            "moving": False,
            "enabled": True,
            "switches": {"forward_switch": False, "reverse_switch": False,
                         "home_switch": False},
        }
        for letter, slit in SC.AXIS_NAMES.items()
    }

    span = SC.LOG_AMP_V_AT_1MA - SC.LOG_AMP_V_AT_1NA
    volts: dict = {}
    for ain, slit in SC.LABJACK_CHANNEL_MAP.items():
        # exact inverse of voltage_to_current
        volts[ain] = SC.LOG_AMP_V_AT_1NA + span * math.log10(
            SLIT_CURRENTS_A[slit] / 1e-9) / 6.0
    for amp in SC.AMP_CHANNEL_MAP:
        volts[SC.AMP_CHANNEL_MAP[amp]["current"]] = AMP_CURRENT_MONITOR_V

    t = np.arange(STREAM_SAMPLES) * STREAM_PERIOD_S
    waves: dict = {}
    for axis, (peak, freq) in AXIS_DRIVE.items():
        tri = 2.0 * np.abs(2.0 * ((t * freq) % 1.0) - 1.0) - 1.0
        # Monitor volts == kV at VOLTAGE_MONITOR_KV_PER_VOLT = 1.0
        waves[SC.AMP_CHANNEL_MAP[f"{axis}+"]["voltage"]] = peak * tri
        waves[SC.AMP_CHANNEL_MAP[f"{axis}-"]["voltage"]] = -peak * tri

    payload = window_payload(volts, t=1.0, samples=STREAM_SAMPLES,
                             sample_period=STREAM_PERIOD_S, waveforms=waves,
                             profile="FULL")

    readback: dict = {}
    setpoints: dict = {}
    for axis, (peak, freq) in AXIS_DRIVE.items():
        for key, phase in zip(AXIS_CHANNELS[axis], (0.0, 180.0)):
            readback[key] = {"shape": "RAMP", "freq": freq, "amp": 2.0 * peak,
                             "offset": 0.0, "phase": phase, "output": True,
                             "load": "INFinity", "load_ohms": math.inf}
        setpoints[axis] = {"amp_vpp": 2.0 * peak, "freq_hz": freq,
                           "output_on": True}

    vacuum = VacuumState(
        timestamp=time.time(),
        vgc_readings=[VgcReading("IG", CHAMBER_TORR, "", "OK"),
                      VgcReading("CG1", FORELINE_TORR, "", "OK")],
        vgc_connected=True, units_vgc="Torr")

    return Scene(motor_poll=motor_poll, zeroed=True, labjack_payload=payload,
                 funcgen_connected={"A": True, "B": True},
                 funcgen_timebase={"A": "INT", "B": "EXT"},
                 funcgen_readback=readback, axis_setpoints=setpoints,
                 vacuum=vacuum)


def push_scene(beamline, scene: Scene) -> None:
    """Feed *scene* through Beamline's public surface only.

    `raw_window_ready` is deliberately not emitted: nothing on the Overview
    needs raw volts, and its consumers are the calibration/characterization
    runners.
    """
    beamline.ingest_motor_poll(scene.motor_poll, scene.zeroed)
    beamline.ingest_labjack_window(scene.labjack_payload, active_profile="FULL")
    beamline.ingest_funcgen_readback(scene.funcgen_connected,
                                     scene.funcgen_timebase,
                                     scene.funcgen_readback)
    beamline.vacuum_changed.emit(
        dataclasses.replace(scene.vacuum, timestamp=time.time()))


class ScreenshotFeed:
    """Re-push the scene on a timer until any real instrument reports."""

    _DATA_SIGNALS = ("motors_changed", "logamps_changed", "amps_changed",
                     "funcgens_changed", "vacuum_changed")
    _CONNECT_SIGNALS = ("labjack_connected", "cup_connected")

    def __init__(self, beamline, scene: Scene,
                 on_stop: Callable[[str], None] | None = None):
        self._beamline = beamline
        self._scene = scene
        self._on_stop = on_stop
        self._pushing = False
        self._active = False
        self._watchers: list[tuple[object, Callable]] = []
        self._timer = QTimer()
        self._timer.setInterval(REPUSH_MS)
        self._timer.timeout.connect(self.push)

    @property
    def active(self) -> bool:
        return self._active

    def start(self) -> None:
        self._active = True
        self.push()
        for name in self._DATA_SIGNALS:
            self._watch(name, self._data_watcher(name))
        for name in self._CONNECT_SIGNALS:
            self._watch(name, lambda *_a: self.stop("a real instrument connected"))
        self._timer.start()

    def push(self) -> None:
        self._pushing = True
        try:
            push_scene(self._beamline, self._scene)
        finally:
            self._pushing = False

    def stop(self, reason: str) -> None:
        if not self._active:
            return
        self._active = False
        self._timer.stop()
        for signal, slot in self._watchers:
            signal.disconnect(slot)
        self._watchers.clear()
        if self._on_stop is not None:
            self._on_stop(reason)

    def _watch(self, name: str, slot: Callable) -> None:
        signal = getattr(self._beamline, name)
        signal.connect(slot)
        self._watchers.append((signal, slot))

    def _data_watcher(self, name: str) -> Callable:
        def slot(*_a) -> None:
            if not self._pushing:
                self.stop(f"real {name} data arrived")
        return slot


def start_screenshot_mode(win) -> ScreenshotFeed:
    scene = build_scene()
    win.setWindowTitle(win.windowTitle() + TITLE_SUFFIX)
    win.overview_tab.connect_all_requested.disconnect()
    # Persists only into the isolated folder main() created.
    win.beamline.set_interlock_gauges({CHAMBER_GAUGE})
    for axis in ("X", "Y"):
        win.beamline.funcgen_setpoints.update_axis(axis, **scene.axis_setpoints[axis])
    win.overview_tab.panel_area.arrange(SCREENSHOT_LAYOUT)

    def stopped(reason: str) -> None:
        win.setWindowTitle("Right Beam Line DAQ — SIMULATION STOPPED: " + reason)

    feed = ScreenshotFeed(win.beamline, scene, on_stop=stopped)
    feed.start()
    return feed


def main(argv: list[str] | None = None) -> int:
    root = Path(tempfile.mkdtemp(prefix="rbl-screenshot-"))
    isolate_environment(root)
    print(f"screenshot mode: all app files go to {root}")
    logging.basicConfig(level=logging.INFO)

    from rbl.gui.app import MainWindow, make_application

    app = make_application(sys.argv if argv is None else ["rbl-screenshot", *argv])
    win = MainWindow()
    feed = start_screenshot_mode(win)
    win.showMaximized()
    rc = app.exec()
    if feed.active:
        feed.stop("window closed")
    return rc


if __name__ == "__main__":
    sys.exit(main())
