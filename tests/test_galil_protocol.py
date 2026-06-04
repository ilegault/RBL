"""
Exhaustive tests for the Galil DMC-4103 ASCII protocol and command builders.

A FakeGalilSocket stands in for the real TCP socket: it records every byte
sent and replays scripted responses, so we can assert both the *exact command
strings* the driver emits and that the ':'/'?'/TC1 protocol is parsed
correctly — all without any hardware.
"""
import socket
import threading
import time
from collections import deque

import pytest

from rbl.hardware.galil_driver import GalilController, GalilError
from rbl.hardware import slit_config as SC


# ── Fake socket ──────────────────────────────────────────────────────────────

class FakeGalilSocket:
    """Scriptable stand-in for a Galil TCP socket.

    responses: maps a command (no trailing '\\r') to either
        - a string  -> success; recv returns "<string>:"
        - ('?', tc1_text) -> error; recv returns "?", then TC1 returns tc1_text
    Unmapped commands succeed with "0:".
    """

    def __init__(self, responses=None):
        self.responses = responses or {}
        self.sent = []                 # every command string sent (stripped of \r)
        self._recv_queue = deque()
        self._pending_tc1 = "0 Unknown error"
        self.closed = False
        self.timeout = None

    def settimeout(self, t):
        self.timeout = t

    def sendall(self, data):
        text = data.decode("ascii")
        cmd = text.rstrip("\r")
        self.sent.append(cmd)
        if cmd == "TC1":
            self._recv_queue.append((self._pending_tc1 + ":").encode("ascii"))
            return
        spec = self.responses.get(cmd, "0")
        if isinstance(spec, tuple) and spec and spec[0] == "?":
            self._pending_tc1 = spec[1]
            self._recv_queue.append(b"?")
        else:
            self._recv_queue.append((str(spec) + ":").encode("ascii"))

    def recv(self, bufsize):
        if self._recv_queue:
            return self._recv_queue.popleft()
        return b""    # connection closed

    def close(self):
        self.closed = True


def make_galil(responses=None):
    g = GalilController()
    g.sock = FakeGalilSocket(responses)
    g.ip = "127.0.0.1"
    return g


# ── Core cmd() protocol ──────────────────────────────────────────────────────

class TestCmdProtocol:
    def test_success_returns_stripped_text(self):
        g = make_galil({"TH": "DMC4103 rev 1.2"})
        assert g.cmd("TH") == "DMC4103 rev 1.2"

    def test_success_appends_carriage_return(self):
        g = make_galil()
        g.cmd("MO")
        assert g.sock.sent[0] == "MO"   # FakeSocket strips the \r it received

    def test_empty_success_response(self):
        g = make_galil({"ST": ""})
        assert g.cmd("ST") == ""

    def test_error_raises_galil_error_with_code(self):
        g = make_galil({"PA 99999": ("?", "22 Begin not possible/soft limit")})
        with pytest.raises(GalilError) as exc:
            g.cmd("PA 99999")
        assert exc.value.code == 22
        assert "soft limit" in exc.value.msg.lower()

    def test_error_sends_tc1(self):
        g = make_galil({"BG A": ("?", "7 Command not valid while running")})
        with pytest.raises(GalilError):
            g.cmd("BG A")
        assert "TC1" in g.sock.sent

    def test_error_without_numeric_code_has_none_code(self):
        g = make_galil({"XQ": ("?", "weird non-numeric reason")})
        with pytest.raises(GalilError) as exc:
            g.cmd("XQ")
        assert exc.value.code is None

    def test_closed_connection_raises_connection_error(self):
        g = make_galil()
        # Drain so recv returns b"" -> closed
        g.sock._recv_queue.clear()

        class ClosedSock(FakeGalilSocket):
            def recv(self, bufsize):
                return b""

        g.sock = ClosedSock()
        with pytest.raises(ConnectionError):
            g.cmd("TH")

    def test_timeout_raises_connection_error(self):
        g = make_galil()

        class TimeoutSock(FakeGalilSocket):
            def recv(self, bufsize):
                raise socket.timeout("timed out")

        g.sock = TimeoutSock()
        with pytest.raises(ConnectionError):
            g.cmd("TH")

    def test_cmd_when_not_connected_raises(self):
        g = GalilController()
        with pytest.raises(ConnectionError):
            g.cmd("TH")


# ── Reads ────────────────────────────────────────────────────────────────────

class TestReads:
    def test_get_position_parses_negative_float(self):
        g = make_galil({"MG _RPA": "-4096.0000"})
        assert g.get_position("A") == -4096

    def test_get_position_rounds(self):
        g = make_galil({"MG _RPB": "100.7"})
        assert g.get_position("B") == 101

    def test_is_moving_true(self):
        g = make_galil({"MG _BGA": "1.0000"})
        assert g.is_moving("A") is True

    def test_is_moving_false(self):
        g = make_galil({"MG _BGA": "0.0000"})
        assert g.is_moving("A") is False

    def test_is_motor_off_true(self):
        g = make_galil({"MG _MOA": "1.0000"})
        assert g.is_motor_off("A") is True

    def test_is_motor_off_false(self):
        g = make_galil({"MG _MOC": "0.0000"})
        assert g.is_motor_off("C") is False

    def test_soft_limits(self):
        g = make_galil({"MG _FLA": "500000.0", "MG _BLA": "-500000.0"})
        lim = g.get_soft_limits("A")
        assert lim["forward_counts"] == 500000
        assert lim["back_counts"] == -500000

    def test_switches_are_active_low(self):
        # Galil reports 0 when a switch is tripped, 1 when open.
        g = make_galil({"MG _LFA": "0", "MG _LRA": "1", "MG _HMA": "0"})
        sw = g.get_switch_states("A")
        assert sw["forward_switch"] is True    # 0 -> tripped
        assert sw["reverse_switch"] is False   # 1 -> open
        assert sw["home_switch"] is True       # 0 -> tripped

    def test_switches_all_open(self):
        g = make_galil({"MG _LFD": "1", "MG _LRD": "1", "MG _HMD": "1"})
        sw = g.get_switch_states("D")
        assert sw == {"forward_switch": False,
                      "reverse_switch": False,
                      "home_switch": False}


# ── Motion command builders (exact wire format) ──────────────────────────────

class TestMotionCommands:
    @pytest.mark.parametrize("axis,prefix", [("A", ""), ("B", ","),
                                             ("C", ",,"), ("D", ",,,")])
    def test_move_absolute_prefix_and_begin(self, axis, prefix):
        g = make_galil()
        g.move_absolute(axis, 1234)
        assert g.sock.sent == [f"PA {prefix}1234", f"BG {axis}"]

    @pytest.mark.parametrize("axis,prefix", [("A", ""), ("B", ","),
                                             ("C", ",,"), ("D", ",,,")])
    def test_move_relative_prefix_and_begin(self, axis, prefix):
        g = make_galil()
        g.move_relative(axis, -500)
        assert g.sock.sent == [f"PR {prefix}-500", f"BG {axis}"]

    def test_jog_start_positive(self):
        g = make_galil()
        g.jog_start("A", 500)
        assert g.sock.sent == ["JG 500", "BG A"]

    def test_jog_start_negative_on_axis_c(self):
        g = make_galil()
        g.jog_start("C", -250)
        assert g.sock.sent == ["JG ,,-250", "BG C"]

    def test_stop_single_axis(self):
        g = make_galil()
        g.stop("B")
        assert g.sock.sent == ["ST B"]

    def test_define_zero(self):
        g = make_galil()
        g.define_zero("D")
        assert g.sock.sent == ["DP ,,,0"]

    def test_enable_disable(self):
        g = make_galil()
        g.enable("AB")
        g.disable("CD")
        assert g.sock.sent == ["SH AB", "MO CD"]

    def test_set_speed(self):
        g = make_galil()
        g.set_speed("B", 1800)
        assert g.sock.sent == ["SP ,1800"]

    def test_set_accel_sets_both_ac_and_dc(self):
        g = make_galil()
        g.set_accel("A", 25600)
        assert g.sock.sent == ["AC 25600", "DC 25600"]

    def test_begin_home_sequence(self):
        g = make_galil()
        g.begin_home("A", 900)
        # SP, JG (negative = toward home), HM, BG  — in that order
        assert g.sock.sent == ["SP 900", "JG -900", "HM A", "BG A"]

    def test_begin_home_direction_is_negative(self):
        g = make_galil()
        g.begin_home("B", 450)
        jg = [c for c in g.sock.sent if c.startswith("JG")][0]
        assert "-450" in jg


class TestAbort:
    def test_abort_sends_ab(self):
        g = make_galil()
        g.abort()
        assert g.sock.sent == ["AB"]

    def test_abort_never_raises_even_on_error(self):
        g = make_galil({"AB": ("?", "1 some error")})
        # abort must swallow GalilError so the e-stop button can't itself fail
        g.abort()   # should not raise

    def test_abort_never_raises_when_disconnected(self):
        g = GalilController()
        g.abort()   # not connected -> still must not raise


# ── Startup sequence ─────────────────────────────────────────────────────────

class TestStartupSequence:
    def test_full_sequence_order_and_content(self):
        g = make_galil()
        g.startup_sequence(axes="ABCD", speed=1000, accel=25600)
        expected = [
            f"CN {SC.CN_CONFIG}",
            "MT -2.5,-2.5,-2.5,-2.5",
            "YA 2,2,2,2",
            "LC 1,1,1,1",
            "AC 25600,25600,25600,25600",
            "DC 25600,25600,25600,25600",
            "SP 1000,1000,1000,1000",
            "ST",
            "MO",
            "AG 3,3,3,3",
            "SH ABCD",
        ]
        assert g.sock.sent == expected

    def test_sequence_scales_to_two_axes(self):
        g = make_galil()
        g.startup_sequence(axes="AB", speed=900, accel=12800)
        # Per-axis replicated values must match the axis count
        mt = [c for c in g.sock.sent if c.startswith("MT")][0]
        assert mt == "MT -2.5,-2.5"
        sh = [c for c in g.sock.sent if c.startswith("SH")][0]
        assert sh == "SH AB"

    def test_sequence_aborts_on_error(self):
        # If MT fails, the error must propagate (caller handles disconnect).
        g = make_galil({"MT -2.5,-2.5,-2.5,-2.5": ("?", "1 bad MT")})
        with pytest.raises(GalilError):
            g.startup_sequence(axes="ABCD")


# ── Lifecycle ────────────────────────────────────────────────────────────────

class TestLifecycle:
    def test_disconnect_closes_socket(self):
        g = make_galil()
        sock = g.sock
        g.disconnect()
        assert sock.closed is True
        assert g.sock is None
        assert not g.connected

    def test_disconnect_is_idempotent(self):
        g = make_galil()
        g.disconnect()
        g.disconnect()   # must not raise
        assert not g.connected

    def test_thread_safety_serializes_commands(self):
        """The controller lock must prevent two threads' send/recv from
        interleaving on the shared socket."""

        class SerializingSocket(FakeGalilSocket):
            def __init__(self):
                super().__init__()
                self.active = 0
                self.max_active = 0
                self._c = threading.Lock()

            def sendall(self, data):
                with self._c:
                    self.active += 1
                    self.max_active = max(self.max_active, self.active)
                time.sleep(0.003)
                super().sendall(data)

            def recv(self, bufsize):
                time.sleep(0.003)
                out = super().recv(bufsize)
                with self._c:
                    self.active -= 1
                return out

        g = GalilController()
        g.sock = SerializingSocket()
        g.ip = "x"

        def worker():
            for _ in range(10):
                g.cmd("MG _RPA")

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert g.sock.max_active == 1, "commands interleaved — lock failed"
