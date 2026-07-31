"""
galil_driver.py
Raw TCP socket interface to a Galil DMC-4103 controller.

Galil ASCII protocol on port 23:
  - Send:    command followed by '\\r'
  - Receive: response terminated by ':' (success) or '?' (error)
  - On '?':  send 'TC1' to retrieve the human-readable error reason

Thread-safe via an internal lock.
"""
import socket
import threading


class GalilError(RuntimeError):
    """A DMC command returned '?'. .code is the Galil error number if parseable."""
    def __init__(self, command: str, msg: str, code: int = None):
        super().__init__(f"{command} -> {msg}")
        self.command = command
        self.msg     = msg
        self.code    = code


class GalilController:
    """Raw TCP socket interface to a Galil DMC controller.

    Lifecycle:
        g = GalilController()
        g.connect("192.168.42.1")
        g.startup_sequence()          # CN, MT, YA, SH ABCD, SP, AC
        g.get_position("A")           # -> int counts
        g.move_absolute("A", 5000)
        g.disconnect()
    """
    DEFAULT_PORT = 23

    def __init__(self):
        self.sock = None
        self.lock = threading.Lock()
        self.ip   = None
        self.port = self.DEFAULT_PORT
        # Limit-switch polarity as the CONTROLLER reports it (_CN0), learned in
        # startup_sequence. None until then — get_switch_states falls back to
        # the configured value, which is all there is to go on before the link
        # has been set up.
        self.limits_active_high = None

    # ---- Lifecycle -------------------------------------------------------

    def connect(self, ip: str, port: int = DEFAULT_PORT, timeout: float = 3.0):
        """Open the socket. Closes any previous connection first."""
        self.disconnect()
        self.sock = socket.create_connection((ip, port), timeout=timeout)
        self.sock.settimeout(2.0)
        self.ip   = ip
        self.port = port

    def disconnect(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    @property
    def connected(self) -> bool:
        return self.sock is not None

    # ---- Core command ----------------------------------------------------

    def cmd(self, command: str) -> str:
        """Send one DMC command, return stripped response, raise GalilError on '?'."""
        if not self.connected:
            raise ConnectionError("Galil not connected")
        with self.lock:
            try:
                self.sock.sendall((command + "\r").encode("ascii"))
                buf = b""
                while not (buf.endswith(b":") or buf.endswith(b"?")):
                    chunk = self.sock.recv(4096)
                    if not chunk:
                        raise ConnectionError("Galil closed the connection")
                    buf += chunk
                if buf.endswith(b"?"):
                    self.sock.sendall(b"TC1\r")
                    err = b""
                    while not err.endswith(b":"):
                        chunk = self.sock.recv(4096)
                        if not chunk:
                            raise ConnectionError("Galil closed during TC1")
                        err += chunk
                    err_text = err[:-1].decode("ascii", errors="replace").strip()
                    code = None
                    parts = err_text.split(None, 1)
                    if parts and parts[0].isdigit():
                        code = int(parts[0])
                    raise GalilError(command, err_text, code=code)
                return buf[:-1].decode("ascii", errors="replace").strip()
            except socket.timeout as e:
                raise ConnectionError(f"Timeout on {command!r}") from e

    # ---- Startup ---------------------------------------------------------

    def startup_sequence(self, axes: str = "ABCD",
                         speed: int = 1800, accel: int = 25600):
        """Full initialization sequence per the 2HA075520 spec email.

        Sends: CN, MT (step motor), YA (1/2 step), LC (low-current hold),
        AC/DC, SP, ST, MO, AG, then SH to enable the specified axes.
        Call once after connect().
        """
        from rbl.config import hardware_config as SC
        n = len(axes)

        def rep(v):
            return ",".join(str(v) for _ in range(n))

        self.cmd(f"CN {SC.CN_CONFIG}")
        self.cmd(f"MT {rep(SC.MOTOR_TYPE)}")
        self.cmd(f"YA {rep(SC.STEP_RESOLUTION)}")
        self.cmd(f"LC {rep(SC.LOW_CURRENT_ON)}")
        self.cmd(f"AC {rep(accel)}")
        self.cmd(f"DC {rep(accel)}")
        self.cmd(f"SP {rep(speed)}")
        self.cmd("ST")
        self.cmd("MO")
        self.cmd(f"AG {rep(SC.AMP_GAIN)}")
        self.cmd(f"SH {axes}")
        self.limits_active_high = self.read_limit_polarity()

    def read_limit_polarity(self):
        """What the controller says its limit switches are (`_CN0`), or None.

        `_CN0` holds the limit-switch configuration: 1 for active high, -1 for
        active low. Asking beats assuming — the reading is what
        get_switch_states interprets every operand against, and the one case
        worth catching is a controller that did not take the CN we sent.

        Returns None rather than raising: a controller that will not answer
        this should still connect, and the configured value covers it.
        """
        try:
            return float(self.cmd("MG _CN0")) > 0.0
        except Exception:
            return None

    # ---- Reads -----------------------------------------------------------

    def get_position(self, axis: str) -> int:
        """Current reference position of axis, in counts (MG _RPx)."""
        return int(round(float(self.cmd(f"MG _RP{axis}"))))

    def is_moving(self, axis: str) -> bool:
        return float(self.cmd(f"MG _BG{axis}")) > 0.5

    def get_soft_limits(self, axis: str) -> dict:
        """Read burned soft limits FL (forward) and BL (back) from flash."""
        return {
            "forward_counts": int(round(float(self.cmd(f"MG _FL{axis}")))),
            "back_counts":    int(round(float(self.cmd(f"MG _BL{axis}")))),
        }

    def get_switch_states(self, axis: str) -> dict:
        """Hardware limit + home switch states (True = active/tripped).

        The limit operands' polarity is set by CN's first argument, so it is
        not a constant and must not be written as one: with `CN m=1` (this
        app's configuration) `_LFn`/`_LRn` read 1 when the switch is ACTIVE,
        and with `m=-1` they read 0. This used to test both against "< 0.5"
        unconditionally, which is the m=-1 reading — so every limit was
        reported exactly backwards, and a clear axis looked like one sitting on
        its limit.

        `limits_active_high` is what the CONTROLLER reported at startup
        (`_CN0`), falling back to what the config says we sent. Reading it back
        rather than assuming it is the point: a controller that rejected or
        never received the CN is exactly the case where the two disagree.

        The home switch is separate — CN's n argument is a search direction,
        not a polarity — so its reading comes from a config constant that
        records how the switch is wired. See HOME_SWITCH_ACTIVE_HIGH.
        """
        from rbl.config import hardware_config as SC

        limits_high = (SC.LIMIT_SWITCHES_ACTIVE_HIGH
                       if self.limits_active_high is None
                       else self.limits_active_high)

        def active(operand: str, active_high: bool) -> bool:
            value = float(self.cmd(f"MG {operand}{axis}"))
            return value > 0.5 if active_high else value < 0.5

        return {
            "forward_switch": active("_LF", limits_high),
            "reverse_switch": active("_LR", limits_high),
            "home_switch":    active("_HM", SC.HOME_SWITCH_ACTIVE_HIGH),
        }

    def is_motor_off(self, axis: str) -> bool:
        """Returns True if the axis is de-energised (MO state, _MO=1)."""
        return float(self.cmd(f"MG _MO{axis}")) > 0.5

    def model_info(self) -> str:
        return self.cmd("TH")

    # ---- Multi-axis argument builder --------------------------------------

    @staticmethod
    def axis_vector(axes: str, value) -> str:
        """Galil's positional argument list over ABCD, for a command sent to
        several axes at once.

        Galil addresses axes by POSITION in the argument list, not by name, so
        a value for C is the third slot and the slots before it must exist even
        when empty: `SP ,,225` is C only. Building that here — once, from an
        axis set — is what lets the multi-axis routine send one `SP` for four
        axes instead of four, without any caller doing comma arithmetic.

            axis_vector("ABCD", 225) -> "225,225,225,225"
            axis_vector("AC",   225) -> "225,,225"
        """
        slots = [str(value) if a in axes else "" for a in "ABCD"]
        return ",".join(slots).rstrip(",")

    # ---- Motion ----------------------------------------------------------

    def move_absolute(self, axis: str, position_counts: int):
        prefix = "," * "ABCD".index(axis)
        self.cmd(f"PA {prefix}{position_counts}")
        self.cmd(f"BG {axis}")

    def move_relative(self, axis: str, delta_counts: int):
        prefix = "," * "ABCD".index(axis)
        self.cmd(f"PR {prefix}{delta_counts}")
        self.cmd(f"BG {axis}")

    def jog_start(self, axis: str, signed_speed: int):
        """Begin continuous jog at signed counts/sec. Stop with .stop(axis)."""
        # Galil positional syntax: A→"JG 500", B→"JG ,500", C→"JG ,,500", D→"JG ,,,500"
        prefix = "," * "ABCD".index(axis)
        self.cmd(f"JG {prefix}{signed_speed}")
        self.cmd(f"BG {axis}")

    def stop(self, axis: str):
        """Decelerated stop on one axis."""
        self.cmd(f"ST {axis}")

    def abort(self):
        """Emergency stop — all axes, immediate. Never raises."""
        try:
            self.cmd("AB")
        except Exception:
            pass

    def enable(self, axes: str = "ABCD"):
        """SH — energise specified axes (e.g. 'A', 'AB', 'ABCD')."""
        self.cmd(f"SH {axes}")

    def disable(self, axes: str = "ABCD"):
        """MO — de-energise specified axes."""
        self.cmd(f"MO {axes}")

    def define_zero(self, axis: str):
        """DP — define current position as zero."""
        prefix = "," * "ABCD".index(axis)
        self.cmd(f"DP {prefix}0")

    def set_speed(self, axis: str, speed_counts_per_sec: int):
        prefix = "," * "ABCD".index(axis)
        self.cmd(f"SP {prefix}{speed_counts_per_sec}")

    def set_accel(self, axis: str, accel_counts_per_sec2: int):
        prefix = "," * "ABCD".index(axis)
        self.cmd(f"AC {prefix}{accel_counts_per_sec2}")
        self.cmd(f"DC {prefix}{accel_counts_per_sec2}")

    def begin_home(self, axis: str, speed: int, fine_speed: int = None):
        """Issue the Galil HM (home) sequence, then BG to run it.

        Returns immediately — motion runs asynchronously. Call is_moving() to
        poll completion, then define_zero(): on a STEPPER, HM does not set
        position 0 itself. Per the HM reference, the third stage — the one that
        latches an encoder index pulse and defines it as zero — is servo-only,
        and "for stepper mode operation, the sequence consists of the first two
        stages". So the zero is ours to define, and DP is how.

        TWO SPEEDS, because HM's two stages use two different ones:
          SP  sets stage 1, the fast search that runs until the home input
              CHANGES STATE, then decelerates to a stop.
          HV  sets stage 2, where the motor reverses and re-approaches that
              same transition slowly, stopping instantaneously on it.
        Stage 2 is the one that fixes where "home" ends up, so HV — not SP —
        is what a homing routine has to turn down for repeatability. It
        defaults to `speed` here only so a caller that does not care still gets
        a defined value rather than whatever HV was left at.

        DIRECTION IS NOT OURS TO CHOOSE. The reference is explicit: "the
        direction for this first stage is determined by the initial state of
        the homing input", i.e. HM works out for itself which way home is from
        whether the axis is currently on the switch. The JG below therefore
        does NOT steer the search — it is left in only because it puts the axis
        in a known jog mode before HM replaces the profile, which is the idiom
        the rest of this driver was written against.
        """
        prefix = "," * "ABCD".index(axis)
        fine = speed if fine_speed is None else fine_speed
        self.cmd(f"SP {prefix}{speed}")   # stage 1: fast search
        self.cmd(f"HV {prefix}{fine}")    # stage 2: slow re-approach — sets the zero
        self.cmd(f"JG {prefix}{-speed}")
        self.cmd(f"HM {axis}")
        self.cmd(f"BG {axis}")

    def set_home_velocity(self, axis: str, speed_counts_per_sec: int):
        """HV — the speed of HM's second stage (the slow re-approach)."""
        prefix = "," * "ABCD".index(axis)
        self.cmd(f"HV {prefix}{speed_counts_per_sec}")

    # ---- Multi-axis motion -------------------------------------------------
    #
    # The HM reference's own idiom: "HM Set Homing Mode for all axes / BG Home
    # all axes". One HM and one BG start every axis together, sequenced by the
    # CONTROLLER, instead of four host threads interleaving their own HM/BG
    # pairs on a single socket and hoping the ordering survives the trip.

    def begin_home_multi(self, axes: str, speed: int, fine_speed: int = None):
        """HM + BG across `axes` — every axis homing under one command.

        Same five commands as begin_home, with every argument a positional
        vector instead of a single slot, so the axes are configured and then
        released together rather than one after another. See begin_home for
        what SP and HV each do to HM's two stages, and why direction is not
        ours to pick.
        """
        fine = speed if fine_speed is None else fine_speed
        self.cmd(f"SP {self.axis_vector(axes, speed)}")
        self.cmd(f"HV {self.axis_vector(axes, fine)}")
        self.cmd(f"JG {self.axis_vector(axes, -speed)}")
        self.cmd(f"HM {axes}")
        self.cmd(f"BG {axes}")

    def jog_start_multi(self, axes: str, signed_speed: int):
        """Begin a jog on every axis in `axes` at one signed speed."""
        self.cmd(f"JG {self.axis_vector(axes, signed_speed)}")
        self.cmd(f"BG {axes}")

    def move_relative_multi(self, axes: str, delta_counts: int):
        self.cmd(f"PR {self.axis_vector(axes, delta_counts)}")
        self.cmd(f"BG {axes}")

    def define_zero_multi(self, axes: str):
        self.cmd(f"DP {self.axis_vector(axes, 0)}")

    def set_speed_multi(self, axes: str, speed_counts_per_sec: int):
        self.cmd(f"SP {self.axis_vector(axes, speed_counts_per_sec)}")

    def set_home_velocity_multi(self, axes: str, speed_counts_per_sec: int):
        self.cmd(f"HV {self.axis_vector(axes, speed_counts_per_sec)}")


# --- Self-test (no hardware) -------------------------------------------------

if __name__ == "__main__":
    e = GalilError("PA A=99999", "22 Soft limit hit", code=22)
    assert e.code == 22
    assert "Soft limit hit" in e.msg
    assert str(e).startswith("PA A=99999")

    g = GalilController()
    assert not g.connected
    g.disconnect()
    assert not g.connected

    try:
        g.cmd("TH")
        raise AssertionError("Should have raised ConnectionError")
    except ConnectionError:
        pass

    print("[OK] galil_driver self-test passed")
