"""
labjack_driver.py
Thin wrapper around the labjack-ljm Python binding for the T7.

Reads 4 single-ended analog inputs (AIN0-AIN3 by default), one per log-amp
channel. The labjack-ljm package depends on the LJM C library being
installed system-wide (download from labjack.com).
"""
try:
    from labjack import ljm
    LJM_AVAILABLE = True
    _LJM_IMPORT_ERROR = None
except Exception as _e:
    ljm = None
    LJM_AVAILABLE = False
    _LJM_IMPORT_ERROR = str(_e)


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
        for ch in range(4):
            ljm.eWriteName(self.handle, f"AIN{ch}_RANGE", 10.0)
            ljm.eWriteName(self.handle, f"AIN{ch}_NEGATIVE_CH", 199)  # GND single-ended
            ljm.eWriteName(self.handle, f"AIN{ch}_RESOLUTION_INDEX", 8)

    def disconnect(self):
        if self.handle is not None:
            try:
                ljm.close(self.handle)
            except Exception:
                pass
            self.handle = None

    def read_channels(self, channels=("AIN0", "AIN1", "AIN2", "AIN3")) -> dict:
        """Single batched round-trip; returns {name: voltage}."""
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
            for name, v in lj.read_channels().items():
                print(f"  {name} = {v:7.4f} V")
            lj.disconnect()
            print("[OK] labjack_driver hardware test passed")
        except Exception as e:
            print(f"Connect failed (OK if no T7 plugged in): {e}")
            print("[OK] labjack_driver imported (no hardware connected)")
