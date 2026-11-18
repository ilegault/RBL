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

WHY EVERY MOVE SENDS ITS SPEED
------------------------------
The Galil keeps ONE speed register per axis (SP), shared by jog, PA/PR moves and
HM's first stage. A move that sends only PA + BG therefore runs at whatever the
last jog or home left there: jog fast on the Stepper Motors tab, then move from
the Overview and the slit crawls (or the reverse). Every move through Beamline
now sends `SP <slit speed>` immediately before `PA` (`move_axis_counts`), so no
move relies on speed state left on the controller by an earlier jog, home or
connect.

WHO OWNS ZEROED
---------------
Beamline (`_zeroed_axes`) is the single owner of zeroed, per axis. An axis
becomes zeroed when homing finishes, when the operator clicks Zero (`DP`), or
when a `DP` command succeeds in the Command Console. Reconnecting the Galil and
emergency stop clear zeroed for every axis. Limit trips and Motor Off (`MO`) do
not clear it: the controller decelerates and keeps counting on a limit, and a
de-energised stepper re-energises to the same position.

THE FLOOR
---------
The homed position is 0 counts. Once an axis is zeroed, no target below 0
counts is sent from any screen: `floor_refusal()` is checked by `move_slit`
and `move_axis_counts` (the Stepper Motors tab's Move and Jog - go through
the latter). The Overview could previously accept targets from 0.0 mm, up to
126 counts past home, and nothing refused them. The floor applies only to
zeroed axes because before zeroing an axis's counts mean nothing yet (the
controller's power-on zero is arbitrary). Homing and the edge-return test talk
to the driver directly and are exempt: homing must be free to seek past
where a stale zero used to be, and the edge-return test drives to a slit edge
without redefining zero.
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
        # Per-axis slit speed (counts/s) sent before every move; see the
        # module docstring, WHY EVERY MOVE SENDS ITS SPEED.
        self._slit_speed_cps: dict[str, int] = {
            a: SC.DEFAULT_SPEED_COUNTS_PER_SEC for a in SC.AXIS_LETTERS}
        # Shared three-pass homing speeds (counts/s), editable on the Stepper
        # Motors tab and passed into every homing run started after they change.
        self._home_pass_speeds: tuple[int, int, int] = (
            SC.HOME_PASS_SPEEDS_COUNTS_PER_SEC
        )
        # Axes whose zero has been defined since the last connect / E-stop.
        self._zeroed_axes: set[str] = set()

    def mark_zeroed(self, axis_letter: str) -> None:
        self._zeroed_axes.add(axis_letter)

    def clear_zeroed(self) -> None:
        self._zeroed_axes.clear()

    def is_zeroed(self, axis_letter: str) -> bool:
        return axis_letter in self._zeroed_axes

    def all_zeroed(self) -> bool:
        return all(a in self._zeroed_axes for a in SC.AXIS_LETTERS)

    def slit_speed_cps(self, axis_letter: str) -> int:
        return self._slit_speed_cps[axis_letter]

    def set_slit_speed_cps(self, axis_letter: str, cps: int) -> None:
        if cps < 1:
            raise ValueError(f"slit speed must be >= 1 count/s, got {cps}")
        self._slit_speed_cps[axis_letter] = int(cps)

    def home_pass_speeds(self) -> tuple[int, int, int]:
        return self._home_pass_speeds

    def set_home_pass_speeds(self, speeds) -> None:
        parsed = tuple(int(s) for s in speeds)
        if len(parsed) != 3 or any(
            not (1 <= s <= SC.HOME_PASS_SPEED_MAX_COUNTS_PER_SEC) for s in parsed
        ):
            raise ValueError(
                f"home pass speeds must be 3 values in 1..{SC.HOME_PASS_SPEED_MAX_COUNTS_PER_SEC}, "
                f"got {speeds!r}"
            )
        self._home_pass_speeds = (parsed[0], parsed[1], parsed[2])

    def floor_refusal(self, axis_letter: str, counts: int):
        """Refusal message when `counts` is below the homed position of a
        zeroed axis, else None. Un-zeroed axes have no floor."""
        if self.is_zeroed(axis_letter) and counts < 0:
            return (f"{SC.AXIS_NAMES[axis_letter]}: {counts} counts is below the "
                    f"homed position (0 counts) — refused")
        return None

    def _refuse_below_floor(self, axis_letter, counts):
        msg = self.floor_refusal(axis_letter, counts)
        if msg is None:
            return False
        self.log_motor("! " + msg)
        self.command_failed.emit("motors", msg)
        return True

    def move_axis_counts(self, axis_letter: str, counts: int) -> bool:
        """Send one absolute move at the axis's slit speed. Driver exceptions
        propagate: callers handle them. Returns False, without touching the
        driver, when the move is below the floor of a zeroed axis."""
        if self._refuse_below_floor(axis_letter, counts):
            return False
        self.galil.move_absolute_at(axis_letter, counts,
                                    self.slit_speed_cps(axis_letter))
        return True

    # ---- Ingestion ------------------------------------------------------------

    def ingest_motor_poll(self, snapshot: dict):
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
                zeroed=self.is_zeroed(axis_letter),
            )
            # The Galil reports each slit as a distance from centre with no
            # sign; the '-' slits live on the negative side of the axis.
            edges[slit] = abs(pos_mm) if slit.endswith("+") else -abs(pos_mm)
        self._slit_edges_mm = edges
        self.motors_changed.emit(
            MotorState(connected=True, zeroed=self.all_zeroed(), axes=axes))

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
        if self._refuse_below_floor(axis_letter, counts):
            return False
        reachable_mm = SC.counts_to_mm(axis_letter, counts)
        speed = self.slit_speed_cps(axis_letter)
        self.log_motor(f"> SP {axis_letter}={speed} ; PA {axis_letter}={counts} "
                       f"({reachable_mm:+.3f} mm) ; BG {axis_letter}   [{slit}]")
        self.note_slit_target(slit, reachable_mm)
        try:
            return self.move_axis_counts(axis_letter, counts)
        except Exception as e:
            self.log_motor(f"! {slit}: {e}")
            self.command_failed.emit("motors", f"{slit}: {e}")
            return False

    def emergency_stop(self):
        """Abort all motion immediately (Galil AB command) and clear zeroed."""
        self.clear_zeroed()
        if not self.galil.connected:
            return
        try:
            self.galil.abort()
        except Exception as e:
            self.command_failed.emit("motors", f"emergency stop: {e}")
