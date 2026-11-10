# 120: Bench — the BPM scope answers on its serial port

**Status:** ready-for-developer

**Runner:** developer

**Auto-merge:** no

**Blocked by:** None (can start immediately)

**Spec:** none — the scope link is documented in `src/rbl/hardware/tds2012_driver.py`

**An agent must not claim this ticket.**

## What this establishes

Whether the TDS 2012 scope (the BPM readout) answers at all, independently of RBL. It is
RS-232 through the TDS2CMA module and a USB-serial adapter, not VISA, so
`scripts/visa_probe.py` cannot test it. On 2026-11-10 the developer's laptop showed one
FTDI adapter, `COM3` (`USB VID:PID=0403:6001 SER=BG032013A`), which NI-VISA reported as
busy, meaning another program had it open.

## Checklist

- [ ] Scope RS-232 setup (Utility menu) recorded in Comments: baud (19200 expected, 9600
      allowed), flow control **Hardware**, line ending **LF**. Cable confirmed null-modem.
      (by hand)
- [ ] With RBL and every other serial program closed, this prints a reply starting
      `ID TEK/TDS`, run on whichever PC the scope is plugged into, with its COM number:
      `python -c "import serial; s=serial.Serial('COM3',19200,rtscts=True,timeout=2); s.write(b'ID?\n'); print(s.readline())"`.
      Record the exact reply. Empty `b''` → flow control, cable or port; garbage → baud.
      (by hand)
- [ ] The Beam Profiler tab connects on that port and shows a trace. (by hand)
- [ ] If the scope answers by hand but not in RBL, the log lines from the failed connect
      are pasted into Comments, and the developer opens a planning task from them.
      (by hand)

## Comments
