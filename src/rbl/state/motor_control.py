"""
motor_control.py
Beamline's motion half: the Galil DMC-4103, the poll snapshot that becomes a
MotorState, and the single path a slit move takes to the controller.

Split out of beamline.py — see labjack_link.py's module docstring for why
these are mixins rather than separate objects.

WHY MOVES ARE PUBLISHED, NOT JUST SENT
--------------------------------------
There is one Command Console (Stepper Motors tab) and there are two screens a
slit can be moved from (that tab and the Overview). Before `motor_logged` and
`slit_target_changed`, a move commanded on the Overview reached the Galil
without ever appearing in the console and without moving the Stepper Motors
tab's Target box — the operator's own action was invisible on the screen that
exists to show it. Both signals go out from `move_slit()`, the single path to
the controller, so neither screen can miss a command the other made.
"""
from rbl.config import hardware_config as SC
from rbl.hardware.galil_driver import GalilController
from rbl.state.snapshots import AxisSnapshot, MotorState


class MotorControlMixin:
    """Galil ownership, poll ingestion, and the slit command surface.

    Mixed into Beamline. Requires the host class to declare: motors_changed,
    motor_logged, slit_target_changed, command_failed.
    """

    def _init_motors(self):
        self.galil = GalilController()
        # Last-seen slit edges (SIGNED mm), cached so reconstruct_beam() can be
        # called on demand without re-deriving them from a poll.
        self._slit_edges_mm: dict[str, float] = {}

    # ---- Ingestion ------------------------------------------------------------

    def ingest_motor_poll(self, snapshot: dict, zeroed: bool):
        """snapshot: axis letter -> {pos, moving, switches, enabled}, exactly
        what GalilPollWorker.state emits."""
        axes = {}
        edges = {}
        for axis_letter, st in snapshot.items():
            slit = SC.AXIS_NAMES[axis_letter]
            pos_mm = SC.counts_to_mm(axis_letter, st["pos"])
            axes[slit] = AxisSnapshot(
                pos_counts=st["pos"],
                pos_mm=pos_mm,
                moving=st["moving"],
                enabled=st.get("enabled", True),
                switches=st["switches"],
            )
            # The Galil reports each slit as a distance from centre with no
            # sign; the '-' slits live on the negative side of the axis.
            edges[slit] = abs(pos_mm) if slit.endswith("+") else -abs(pos_mm)
        self._slit_edges_mm = edges
        self.motors_changed.emit(MotorState(connected=True, zeroed=zeroed, axes=axes))

    def motors_disconnected(self):
        self._slit_edges_mm = {}
        self.motors_changed.emit(MotorState(connected=False, zeroed=False, axes={}))

    # ---- Command surface ------------------------------------------------------

    def axis_letter_for(self, slit: str):
        """Galil axis letter for a slit label ("X+" -> "A"), or None."""
        return next((a for a, j in SC.AXIS_NAMES.items() if j == slit), None)

    def log_motor(self, line: str):
        """Put one line on the Stepper Motors tab's Command Console.

        Anything that reaches the Galil should say so here, whichever screen
        it came from — that console is the app's record of what was sent, and
        a command missing from it reads as a command that never happened.
        """
        self.motor_logged.emit(line)

    def note_slit_target(self, slit: str, mm: float):
        """Publish a commanded slit target so every screen shows the same one.

        Separate from move_slit() because the Stepper Motors tab issues its
        own move through the driver (it has the soft-limit dialog and the
        counts/mm unit toggle that the Overview deliberately does not), and
        the Overview still has to learn the target that move set.
        """
        self.slit_target_changed.emit(slit, float(mm))

    def move_slit(self, slit: str, mm: float) -> bool:
        """Move one slit to an absolute position in mm.

        `slit` is a slit label ("X+", "X-", "Y+", "Y-"), not a Galil axis
        letter — callers shouldn't need to know the axis mapping.

        Logs to the Command Console and publishes the new target BEFORE
        touching the driver, and does so even when the move is refused: an
        attempt that failed is exactly the thing an operator needs to find in
        the console afterwards.
        """
        axis_letter = self.axis_letter_for(slit)
        if axis_letter is None:
            self.command_failed.emit("motors", f"{slit}: not a valid slit label")
            return False
        if not self.galil.connected:
            self.log_motor(f"! {slit}: move to {mm:+.3f} mm ignored — not connected")
            self.command_failed.emit("motors", f"{slit}: Galil not connected")
            return False

        # Publish the ACHIEVABLE target, not the typed one. A stepper lands on
        # whole counts, so 6.000 mm is commanded as 3653 counts and the slit
        # stops at 6.001 mm. Marking the typed number on the bars would leave
        # every caret sitting a fraction of a step off the position that
        # eventually arrives under it, and would disagree with the Stepper
        # Motors tab, which has always worked in counts.
        counts = SC.mm_to_counts(axis_letter, mm)
        reachable_mm = SC.counts_to_mm(axis_letter, counts)
        self.log_motor(f"> PA {axis_letter}={counts} ({reachable_mm:+.3f} mm) ; "
                       f"BG {axis_letter}   [{slit}]")
        self.note_slit_target(slit, reachable_mm)
        try:
            self.galil.move_absolute(axis_letter, counts)
            return True
        except Exception as e:
            self.log_motor(f"! {slit}: {e}")
            self.command_failed.emit("motors", f"{slit}: {e}")
            return False

    def emergency_stop(self):
        """Abort all motion immediately (Galil AB command)."""
        if not self.galil.connected:
            return
        try:
            self.galil.abort()
        except Exception as e:
            self.command_failed.emit("motors", f"emergency stop: {e}")
