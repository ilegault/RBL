"""
beamline.py
Beamline: the single owner of every instrument (LabJack T7, Galil DMC-4103,
two DG1022Z function generators) and the single place live values get
converted from raw volts/counts into physical units and published as typed
snapshots.

No QWidget holds a driver instance or is responsible for its lifecycle;
tabs reach the driver objects through a thin delegating property/proxy so
their existing call sites don't change, but construction and final teardown
happen here, once.

HOW THIS FILE IS ORGANISED
--------------------------
What remains here is the whole class's business: the signals it publishes,
the order its instruments are set up and torn down in, and the one reading
that needs two subsystems at once (`reconstruct_beam` — the log-amp currents
mean nothing in millimetres until you know where the slits are).

Each instrument's own lifecycle and command surface lives in a mixin beside
this file, because one class holding three of them had grown past 900 lines
and the three barely touch:

    rbl/state/labjack_link.py     T7 handle, stream worker, window ingestion
    rbl/state/funcgen_control.py  DG1022Z pair, readback, the ±5 V interlock
    rbl/state/motor_control.py    Galil, poll ingestion, slit moves

They are mixins rather than separate objects so that `Beamline` stays ONE
class with ONE public API — see labjack_link.py's module docstring. The
signals stay declared here: Qt requires them in a QObject subclass body, and
they are the class's published interface rather than any one half's.
"""
import atexit

from PySide6.QtCore import QObject, Signal

from rbl.hardware import beam_reconstruction as BR
from rbl.state.funcgen_control import FuncGenControlMixin
from rbl.state.hv_interlock_link import HvInterlockLinkMixin
from rbl.state.labjack_link import LabJackLinkMixin
from rbl.state.motor_control import MotorControlMixin
from rbl.state.scope_link import ScopeLinkMixin
from rbl.state.vacuum_link import VacuumLinkMixin
from rbl.util import best_effort


class Beamline(LabJackLinkMixin, FuncGenControlMixin, MotorControlMixin, VacuumLinkMixin,
               ScopeLinkMixin, HvInterlockLinkMixin, QObject):
    motors_changed   = Signal(object)   # MotorState
    logamps_changed  = Signal(object)   # LogAmpState
    amps_changed     = Signal(object)   # AmpState
    funcgens_changed = Signal(object)   # FuncGenState
    timebase_changed = Signal(dict)     # {"A": "INT"/"EXT"/"?"/"—", "B": ...}
    command_failed   = Signal(str, str)  # subsystem, message
    vacuum_changed   = Signal(object)   # VacuumState
    vacuum_error     = Signal(str)
    scope_changed    = Signal(object)   # ScopeState
    scope_error      = Signal(str)

    # Vacuum <-> HV interlock (hv_interlock_link.py). dict:
    # {"state": "ok"|"warn"|"block", "reason": str, "pressure_torr": float,
    #  "pressure_stale": bool, "commanded_kv": float}
    hv_interlock_changed = Signal(object)

    # Motion commanded from ANY screen, published so every screen sees it.
    # See motor_control.py's module docstring for why a move is published and
    # not merely sent.
    motor_logged     = Signal(str)        # one console line, already formatted
    slit_target_changed = Signal(str, float)   # slit label, absolute mm

    # LabJack connection lifecycle. Re-emitted here (rather than reaching into
    # widgets directly) so this class stays Qt-signal-only, no GUI knowledge.
    labjack_connected    = Signal(str)    # serial
    labjack_disconnected_evt = Signal()
    stream_error         = Signal(str)
    profile_changed      = Signal(str)

    # The raw, unconverted LabJackStreamWorker.window_ready payload for every
    # window, re-emitted as-is. logamps_changed/amps_changed carry the
    # converted kV/mA state every tab renders; this exists for a consumer
    # that needs the original volts (e.g. CalibrationRunner's mean/std/min/max
    # over the raw waveform), so it isn't reconstructing volts from an
    # already-converted snapshot.
    raw_window_ready     = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_labjack()
        self._init_funcgens()
        self._init_motors()
        self._init_vacuum()
        self._init_scope()
        self._init_hv_interlock()

        # Last-resort safety net: if the process is torn down without a clean
        # closeEvent (e.g. an unhandled exit), still stop the LabJack stream
        # and close the handle so the T7 is never left in stream mode.
        atexit.register(self._emergency_labjack_shutdown)

    # ---- The one reading that needs two subsystems ------------------------------

    def reconstruct_beam(self, sigma_mm: float, span_x_mm: float = 0.0,
                          span_y_mm: float = 0.0):
        """Beam position from the last-seen currents + slit edges.

        The one call site every consumer (the log-amp tab's beam indicator and
        the Overview tab) uses, so they read the same currents and edges and
        can never disagree about where the beam is — only the assumed spot
        size stays a per-caller / operator setting.

        This is why the two halves are one class: the currents come from the
        LabJack and the edges from the Galil, and neither is a beam position
        without the other.

        Returns None if fewer than all four slit edges are known yet.
        """
        if len(self._slit_edges_mm) < 4:
            return None
        return BR.reconstruct(
            self._log_amp_currents, self._slit_edges_mm, sigma_mm, span_x_mm, span_y_mm
        )

    # ---- Full shutdown (MainWindow.closeEvent) ----------------------------------

    def shutdown(self):
        """Full hardware teardown, in the required order, for app close.

        Order matters: stop the LabJack stream before closing its handle
        (disconnect_labjack already guarantees this), abort Galil motion
        before disconnecting it, and NEVER disable the function generators'
        outputs here — they are meant to retain state after the app exits
        (see FuncGenTab.close_session's docstring; this mirrors it).
        """
        best_effort("disconnect_labjack",  self.disconnect_labjack)
        best_effort("galil.abort",          lambda: self.galil.connected and self.galil.abort())
        best_effort("galil.disconnect",     self.galil.disconnect)
        best_effort("shutdown_vacuum",      self._shutdown_vacuum)
        best_effort("shutdown_scope",       self._shutdown_scope)
        for gen in (self.dg_a, self.dg_b):
            if gen is not None:
                best_effort("funcgen.close", gen.close)
