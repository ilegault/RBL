"""
beamline_snapshot.py
Concise beamline state for the session recorder.

Subscribes to Beamline's signals and builds a compact, digestible summary of
just the numbers that matter: slit aperture, beam current, function-generator
commands, amplifier readings (commanded vs measured), pressure, and scope FWHM.

No raw waveforms, no switch states, no numpy arrays.  The snapshot is what a
human wants to read in a JSON sidecar or CSV — not an internal dataclass dump.

The amp summary is computed AT STORE TIME because the "pk@f" current is derived
from window_ma via a single-bin DFT, and that buffer is a reference to the
stream worker's array.

Threading: all callbacks and snapshot() run on the GUI thread.  No locking.
"""
import dataclasses

from PySide6.QtCore import QObject

from rbl.services.snapshot_json import (
    amp_summary,
    current_summary,
    funcgen_summary,
    pressure_summary,
    scope_summary,
    slit_summary,
)
from rbl.state.beamline import Beamline


class BeamlineSnapshotProvider(QObject):
    """Cache the latest beamline state as concise, digestible summaries.

    Connects to all six Beamline signals at construction time; thereafter
    snapshot() returns a compact, JSON-safe dict of just the numbers that
    matter — no raw waveforms, no switch states, no internal metadata.
    """

    def __init__(self, beamline: Beamline, parent=None):
        super().__init__(parent)

        # Live dataclass refs — kept for amp_summary which needs window_ma.
        self._live_amps = None
        self._live_funcgens = None

        # Derived concise blocks, refreshed on each signal.
        self._slits: dict = {}
        self._currents: dict = {}
        self._funcgen: dict = {}
        self._amps: dict = {}
        self._pressure: dict = {}
        self._scope: dict = {}

        beamline.motors_changed.connect(self._on_motors)
        beamline.logamps_changed.connect(self._on_logamps)
        beamline.amps_changed.connect(self._on_amps)
        beamline.funcgens_changed.connect(self._on_funcgens)
        beamline.scope_changed.connect(self._on_scope)
        beamline.vacuum_changed.connect(self._on_vacuum)

    # ---- signal handlers ---------------------------------------------------

    @staticmethod
    def _is_snap(obj) -> bool:
        return dataclasses.is_dataclass(obj) and not isinstance(obj, type)

    def _on_motors(self, obj) -> None:
        if not self._is_snap(obj):
            return
        try:
            self._slits = slit_summary(obj)
        except Exception:
            pass

    def _on_logamps(self, obj) -> None:
        if not self._is_snap(obj):
            return
        try:
            self._currents = current_summary(obj)
        except Exception:
            pass

    def _on_amps(self, obj) -> None:
        if not self._is_snap(obj):
            return
        self._live_amps = obj
        self._refresh_amp_summary()

    def _on_funcgens(self, obj) -> None:
        if not self._is_snap(obj):
            return
        self._live_funcgens = obj
        try:
            self._funcgen = funcgen_summary(obj)
        except Exception:
            pass
        self._refresh_amp_summary()

    def _on_scope(self, obj) -> None:
        if not self._is_snap(obj):
            return
        try:
            self._scope = scope_summary(obj)
        except Exception:
            pass

    def _on_vacuum(self, obj) -> None:
        if not self._is_snap(obj):
            return
        try:
            self._pressure = pressure_summary(obj)
        except Exception:
            pass

    def _refresh_amp_summary(self) -> None:
        if self._live_amps is None:
            return
        try:
            self._amps = amp_summary(self._live_amps, self._live_funcgens)
        except Exception:
            pass

    # ---- public API --------------------------------------------------------

    def snapshot(self) -> dict:
        """Return a concise, JSON-safe beamline state dict.

        Six top-level keys, each a compact summary:

            slits         aperture width/height in mm, jaw positions, zeroed
            beam_current  per-jaw current in Amps
            funcgen       per-axis commanded shape/freq/amplitude/output
            amplifiers    per-axis commanded-vs-measured kV, current, status
            pressure      gauge readings keyed by label
            scope         connected, FWHM, peak count

        No raw waveforms, no switch states, no bulk buffers.
        """
        return {
            "slits":        dict(self._slits),
            "beam_current": dict(self._currents),
            "funcgen":      dict(self._funcgen),
            "amplifiers":   dict(self._amps),
            "pressure":     dict(self._pressure),
            "scope":        dict(self._scope),
        }
