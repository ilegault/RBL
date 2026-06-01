"""
galil_driver.py
Raw TCP socket interface to a Galil DMC-4103 controller.

Galil ASCII protocol on port 23:
  - Send:    command followed by '\\r'
  - Receive: response terminated by ':' (success) or '?' (error)
  - On '?':  send 'TC1' to retrieve the human-readable error reason

This wrapper is thread-safe via an internal lock — multiple poll threads can
share one instance without colliding on the socket.
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
        g.connect("192.168.1.10")
        g.startup_sequence()          # CN 1, SH ABCD, safe SP/AC
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
                         speed: int = 5000, accel: int = 100000):
        """Send the standard safety / default-motion-params block on connect.

        ALWAYS call once after connect(). CN 1 in particular is volatile — it
        does not survive a power cycle, so it must be sent every time."""
        self.cmd("CN 1")
        self.cmd(f"SH {axes}")
        sp_str = ",".join(str(speed) for _ in axes)
        ac_str = ",".join(str(accel) for _ in axes)
        self.cmd(f"SP {sp_str}")
        self.cmd(f"AC {ac_str}")
        self.cmd(f"DC {ac_str}")

    # ---- Reads -----------------------------------------------------------

    def get_position(self, axis: str) -> int:
        """Current absolute position of axis, in counts."""
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
        """Hardware limit + home switch states (True = active)."""
        return {
            "forward_switch": float(self.cmd(f"MG _LF{axis}")) > 0.5,
            "reverse_switch": float(self.cmd(f"MG _LR{axis}")) > 0.5,
            "home_switch":    float(self.cmd(f"MG _HM{axis}")) > 0.5,
        }

    def model_info(self) -> str:
        return self.cmd("TH")

    # ---- Motion ----------------------------------------------------------

    def move_absolute(self, axis: str, position_counts: int):
        self.cmd(f"PA {axis}={position_counts}")
        self.cmd(f"BG {axis}")

    def move_relative(self, axis: str, delta_counts: int):
        self.cmd(f"PR {axis}={delta_counts}")
        self.cmd(f"BG {axis}")

    def jog_start(self, axis: str, signed_speed: int):
        """Begin continuous jog at signed counts/sec. Stop with .stop(axis)."""
        self.cmd(f"JG {axis}={signed_speed}")
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
        self.cmd(f"SH {axes}")

    def disable(self, axes: str = "ABCD"):
        self.cmd(f"MO {axes}")

    def define_zero(self, axis: str):
        """DP axis=0 — define current position as zero (counts only; does NOT touch
        slit_config zero offset)."""
        self.cmd(f"DP {axis}=0")

    def set_speed(self, axis: str, speed_counts_per_sec: int):
        self.cmd(f"SP {axis}={speed_counts_per_sec}")

    def set_accel(self, axis: str, accel_counts_per_sec2: int):
        self.cmd(f"AC {axis}={accel_counts_per_sec2}")
        self.cmd(f"DC {axis}={accel_counts_per_sec2}")


# --- Self-test (no hardware) -------------------------------------------------

if __name__ == "__main__":
    # GalilError code parsing
    e = GalilError("PA A=99999", "22 Soft limit hit", code=22)
    assert e.code == 22
    assert "Soft limit hit" in e.msg
    assert str(e).startswith("PA A=99999")

    # Lifecycle without hardware
    g = GalilController()
    assert not g.connected
    g.disconnect()  # safe when not connected
    assert not g.connected

    # cmd() raises ConnectionError when not connected
    try:
        g.cmd("TH")
        raise AssertionError("Should have raised ConnectionError")
    except ConnectionError:
        pass

    print("[OK] galil_driver self-test passed")
    print("    Live hardware test happens in Phase 5 via the GUI.")
