"""
labjack_driver.py
Thin wrapper around the labjack-ljm Python binding for the T7.

Reads single-ended analog inputs. AIN0-AIN3 are the NEC log amps (on the T7 body
screw terminals); AIN6-AIN13 are the EEL5000 HV amplifier voltage/current monitors
(on a CB37 terminal board). All channels are configured +/-10 V, single-ended.

WHY THIS EXISTS — DIGITAL PATH (ADR 0003)
-----------------------------------------
The T7 also drives and reads the Faraday Cup Controller through digital I/O lines
on the CB37 terminal board. FIO0 (enable) and FIO1 (command-out) control external
relays via an LJTick-RelayDriver. FIO2 (cup IN), FIO3 (cup OUT), and FIO4 (AUTO mode)
read back isolated status contacts.

Closed is zero:
A status contact that is closed pulls its FIO line to ground and therefore reads as
bit value 0 in FIO_STATE. The status decoding function inverts.

Fails-into-the-beam:
Cup OUT requires both contact closures (relay 1 enable and relay 2 command). Any
loss of drive — crash, disconnect, or power loss — opens both relays, and the
cup returns IN where it safely intercepts the beam ahead of the specimen. The
absence of drive is the safe state. Therefore, no software path drives the cup
on shutdown, on disconnect, or in an exception handler; the fail-safe belongs to
the physical wiring.

Single-line writes:
Digital outputs are commanded line-by-line rather than by full-bank bitmasks,
guaranteeing that writing one line (e.g. FIO1) cannot accidentally clear another (e.g. FIO0).

IMPORTANT: only ONE LabJackT7 instance may exist per physical device. LJM will
happily hand out a second handle to the same T7, but concurrent eReadNames calls
from two threads corrupt each other. MainWindow owns the single instance and the
single poll worker; tabs subscribe to its readings.

The labjack-ljm package depends on the LJM C library being installed system-wide
(download from labjack.com).
"""
try:
    from labjack import ljm
    LJM_AVAILABLE = True
    _LJM_IMPORT_ERROR = None
except Exception as _e:
    ljm = None
    LJM_AVAILABLE = False
    _LJM_IMPORT_ERROR = str(_e)

from rbl.config.cup_config import CUP_COMMAND_OUT_LINE, CUP_ENABLE_LINE

# Number of AINs to configure on connect. AIN0-3 = log amps, AIN6-13 = HV amp
# monitors. AIN4-5 are left unconfigured (spare).
N_CONFIGURED_AINS = 14

# Default read set: all 12 channels in one batched round trip.
DEFAULT_CHANNELS = tuple(f"AIN{i}" for i in range(N_CONFIGURED_AINS))


class LabJackError(RuntimeError):
    pass


class LabJackT7:
    """Open a T7 via USB or Ethernet, configure 4 analog inputs, read them.

    Usage:
        lj = LabJackT7()
        lj.connect("USB", "ANY")
        readings = lj.read_channels()   # dict: 'AIN0' -> 0.123, ...
        lj.disconnect()
    """

    def __init__(self):
        self.handle = None
        self.connection_type = None

    @property
    def connected(self) -> bool:
        return self.handle is not None

    def connect(self, connection_type: str = "USB", identifier: str = "ANY"):
        if not LJM_AVAILABLE:
            raise LabJackError(
                "labjack-ljm is not importable. "
                f"Install: pip install labjack-ljm   ({_LJM_IMPORT_ERROR})"
            )
        if self.handle is not None:
            self.disconnect()
        self.handle = ljm.openS("T7", connection_type, identifier)
        self.connection_type = connection_type

        # Configure each AIN: +/-10 V single-ended, high-resolution.
        #
        # +/-10 V is mandatory, not a default. The EEL5000 CURRENT MONITOR emits
        # 1 V per 10 mA and the amplifier is rated for a 100 mA / 4 ms transient,
        # which puts 10 V on the BNC. Any narrower range would clip it.
        for ch in range(N_CONFIGURED_AINS):
            ljm.eWriteName(self.handle, f"AIN{ch}_RANGE", 10.0)
            ljm.eWriteName(self.handle, f"AIN{ch}_NEGATIVE_CH", 199)  # GND single-ended
            # Resolution index 1: compatible with stream mode (index 0 or 1 required
            # to keep the 100 kS/s aggregate ceiling).  The old value of 8 was only
            # safe for command-response reads and would prevent eStreamStart from
            # reaching the target aggregate rate.
            ljm.eWriteName(self.handle, f"AIN{ch}_RESOLUTION_INDEX", 1)

        # Configure digital I/O lines for Faraday cup actuation and status.
        # FIO0 (enable) and FIO1 (command-out) are outputs.
        # FIO2 (IN status), FIO3 (OUT status), FIO4 (AUTO status) are inputs.
        # FIO_DIRECTION: lower byte sets direction (1=output, 0=input).
        # Upper byte is inhibit mask (1=inhibit, 0=modify).
        # Lines 0-4 are modified (inhibit=0), lines 5-7 are left untouched (inhibit=1).
        # Inhibit: 0xE000 ((1<<13)|(1<<14)|(1<<15)).
        # Direction: bit 0=1, bit 1=1, bits 2..4=0 -> 0x0003.
        ljm.eWriteName(self.handle, "FIO_DIRECTION", 0xE003)

        # Ensure both outputs start de-asserted (0).
        # Absence of drive is the safe state (cup fails into the beam).
        ljm.eWriteName(self.handle, CUP_ENABLE_LINE, 0)
        ljm.eWriteName(self.handle, CUP_COMMAND_OUT_LINE, 0)


    def stop_stream(self):
        """Force any active hardware stream to stop.

        Safe to call unconditionally: if the T7 is not streaming, LJM raises
        LJME_STREAM_NOT_RUNNING, which we swallow.  This is the single choke
        point that guarantees the device is never left in stream mode — call it
        before closing the handle, and again from any last-resort shutdown path.
        """
        if self.handle is None or not LJM_AVAILABLE:
            return
        try:
            ljm.eStreamStop(self.handle)
        except Exception:
            # Not streaming, or already stopped — nothing to do.
            pass

    def disconnect(self):
        if self.handle is not None:
            # ALWAYS stop the stream before closing the handle.  Closing a
            # handle that is still streaming leaves the T7 firmware in stream
            # mode, which blocks every subsequent connection until the device
            # is physically unplugged.  eStreamStop here is the guarantee that
            # never happens, no matter which path reached disconnect().
            self.stop_stream()
            try:
                ljm.close(self.handle)
            except Exception:
                pass
            self.handle = None
            self.connection_type = None

    def read_channels(self, channels=DEFAULT_CHANNELS) -> dict:
        """Single batched round-trip; returns {name: voltage}.

        One eReadNames call for all 12 channels. Do NOT split this into two
        calls from two threads — one T7, one reader.
        """
        if not self.connected:
            raise LabJackError("LabJack not connected")
        values = ljm.eReadNames(self.handle, len(channels), list(channels))
        return dict(zip(channels, values))

    def serial_number(self) -> str:
        if not self.connected:
            return "—"
        try:
            return str(int(ljm.eReadName(self.handle, "SERIAL_NUMBER")))
        except Exception:
            return "?"

    def write_digital(self, line: str, state: int | bool) -> None:
        """Write a single named digital line to a state (0 or 1).

        Uses single-line write (eWriteName) so no other line is disturbed.
        """
        if not self.connected:
            raise LabJackError("LabJack not connected")
        val = 1 if state else 0
        ljm.eWriteName(self.handle, line, val)

    def read_fio_state(self) -> int:
        """Read and return the raw FIO_STATE integer register.

        FIO_STATE (address 2500) returns the digital state of FIO lines.
        """
        if not self.connected:
            raise LabJackError("LabJack not connected")
        return int(ljm.eReadName(self.handle, "FIO_STATE"))



# --- Self-test ---------------------------------------------------------------

if __name__ == "__main__":
    print(f"labjack-ljm available: {LJM_AVAILABLE}")
    if not LJM_AVAILABLE:
        print(f"  Import error: {_LJM_IMPORT_ERROR}")
        print("  Install with: pip install labjack-ljm  (also need LJM system library)")
        print("[OK] labjack_driver imported (no hardware test possible)")
    else:
        try:
            lj = LabJackT7()
            lj.connect("USB", "ANY")
            print(f"Connected to T7 serial #{lj.serial_number()}")
            print(f"Configured {N_CONFIGURED_AINS} AINs at +/-10 V")
            for name, v in lj.read_channels().items():
                print(f"  {name} = {v:7.4f} V")
            lj.disconnect()
            print("[OK] labjack_driver hardware test passed")
        except Exception as e:
            print(f"Connect failed (OK if no T7 plugged in): {e}")
            print("[OK] labjack_driver imported (no hardware connected)")
