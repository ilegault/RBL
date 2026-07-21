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

        All three inputs are active-low: the Galil reports 0 when a switch
        is triggered and 1 when it is open, so active = value < 0.5.
        """
        return {
            "forward_switch": float(self.cmd(f"MG _LF{axis}")) < 0.5,
            "reverse_switch": float(self.cmd(f"MG _LR{axis}")) < 0.5,
            "home_switch":    float(self.cmd(f"MG _HM{axis}")) < 0.5,
        }

    def is_motor_off(self, axis: str) -> bool:
        """Returns True if the axis is de-energised (MO state, _MO=1)."""
        return float(self.cmd(f"MG _MO{axis}")) > 0.5

    def model_info(self) -> str:
        return self.cmd("TH")

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

    def begin_home(self, axis: str, speed: int):
        """Issue the Galil HM (home) command sequence at the given speed.

        Returns immediately — motion runs asynchronously. Call is_moving() to
        poll completion, then define_zero() once the home switch is confirmed.

        Direction is negative (toward 0) — set via JG before HM.
        """
        prefix = "," * "ABCD".index(axis)
        self.cmd(f"SP {prefix}{speed}")
        self.cmd(f"JG {prefix}{-speed}")   # set homing direction: negative
        self.cmd(f"HM {axis}")
        self.cmd(f"BG {axis}")


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
