"""
Simulated slit axes and Galil controller for homing, edge-return, and switch-map tests.

WHY THIS EXISTS
---------------
The behaviour under test in release-edge homing (ADR 0009) is "where does zero
land given where the switch is, including hysteresis, overshoot, and lost
steps?". Scripted MagicMock return values cannot answer that; only a simulated
axis with a real position and switch hysteresis can.
"""
from rbl.hardware.galil_driver import GalilError


class SimSlit:
    """One simulated slit axis with physical position, offset, and switch hysteresis."""

    def __init__(
        self,
        start_at: int = 2000,
        trip_at: int = -3,
        release_at: int = 6,
        reverse_trip_at: int | None = None,
        reverse_release_at: int | None = None,
        overshoot_per_100_cps: int = 1,
        slip_on_jog: dict[int, int] | None = None,
    ):
        self.phys = start_at
        self.offset = 0
        self.trip_at = trip_at
        self.release_at = release_at
        self.reverse_trip_at = trip_at if reverse_trip_at is None else reverse_trip_at
        self.reverse_release_at = (
            release_at if reverse_release_at is None else reverse_release_at
        )
        self.overshoot_per_100_cps = overshoot_per_100_cps
        self.slip_on_jog = dict(slip_on_jog) if slip_on_jog is not None else {}

        self.home_tripped = self.phys <= self.trip_at
        self.reverse_tripped = self.phys <= self.reverse_trip_at
        self.deepest = self.phys
        self.zeroed_at: list[int] = []
        self.stop_code = 1
        self.speed = 0
        self._jog_count = 0

    @property
    def rp(self) -> int:
        """Commanded position (_RP): physical position + controller offset."""
        return self.phys + self.offset

    def _update_switches(self) -> None:
        if self.phys <= self.trip_at:
            self.home_tripped = True
        elif self.phys >= self.release_at:
            self.home_tripped = False

        if self.phys <= self.reverse_trip_at:
            self.reverse_tripped = True
        elif self.phys >= self.reverse_release_at:
            self.reverse_tripped = False

    def _step_by(self, delta: int) -> None:
        self.phys += delta
        if self.phys < self.deepest:
            self.deepest = self.phys
        self._update_switches()


class SimGalil:
    """GalilController-shaped fake backed by per-axis SimSlit instances."""

    def __init__(self, axes: str = "ABCD", **slit_kwargs):
        self.connected = True
        self.slits: dict[str, SimSlit] = {
            axis: SimSlit(**slit_kwargs) for axis in axes
        }
        self.calls: list[tuple[str, tuple]] = []

    def _record(self, name: str, *args) -> None:
        self.calls.append((name, args))

    def get_switch_states(self, axis: str) -> dict:
        self._record("get_switch_states", axis)
        s = self.slits[axis]
        return {
            "forward_switch": False,
            "reverse_switch": s.reverse_tripped,
            "home_switch": s.home_tripped,
        }

    def get_position(self, axis: str) -> int:
        self._record("get_position", axis)
        return self.slits[axis].rp

    def is_moving(self, axis: str) -> bool:
        self._record("is_moving", axis)
        return False

    def get_stop_code(self, axis: str) -> int:
        self._record("get_stop_code", axis)
        return self.slits[axis].stop_code

    def set_speed(self, axis: str, speed_counts_per_sec: int) -> None:
        self._record("set_speed", axis, speed_counts_per_sec)
        self.slits[axis].speed = speed_counts_per_sec

    def stop(self, axis: str) -> None:
        self._record("stop", axis)

    def define_zero(self, axis: str) -> None:
        self._record("define_zero", axis)
        s = self.slits[axis]
        s.zeroed_at.append(s.rp)
        s.offset = -s.phys

    def move_relative(self, axis: str, delta_counts: int) -> None:
        self._record("move_relative", axis, delta_counts)
        s = self.slits[axis]
        if delta_counts > 0:
            s._step_by(delta_counts)
            s.stop_code = 1
            return
        if delta_counts == 0:
            s.stop_code = 1
            return
        if s.reverse_tripped:
            raise GalilError("BG", "22 Begin not possible due to limit switch", 22)
        for _ in range(abs(delta_counts)):
            s._step_by(-1)
            if s.reverse_tripped:
                s.stop_code = 3
                return
        s.stop_code = 1

    def jog_start(self, axis: str, signed_speed: int) -> None:
        self._record("jog_start", axis, signed_speed)
        if signed_speed > 0:
            raise AssertionError("homing never jogs out (positive jog_start)")
        s = self.slits[axis]
        if s.reverse_tripped:
            raise GalilError("BG", "22 Begin not possible due to limit switch", 22)
        s._jog_count += 1
        while not (s.home_tripped or s.reverse_tripped):
            s._step_by(-1)
        overshoot = (abs(signed_speed) // 100) * s.overshoot_per_100_cps
        for _ in range(overshoot):
            s._step_by(-1)
        s.stop_code = 3 if s.reverse_tripped else 4
        if s._jog_count in s.slip_on_jog:
            s.offset -= s.slip_on_jog[s._jog_count]
