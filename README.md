# RBL

Desktop application for planning, simulating, controlling, and evaluating the right beam line in IBL at UW-Madison NEEP

---

## Requirements

### Hardware

| Device | Interface | Driver |
|---|---|---|
| Galil DMC-4103 motion controller | TCP/IP (port 23) | Built-in ASCII socket protocol |
| LabJack T7 analog input | USB or Ethernet | `labjack-ljm` Python package + LJM system library |
| LabJack CB37 terminal board | DB37 ribbon to T7 | Passive breakout — no driver |
| 4 x EEL5000.20.100 HV amplifier | BNC monitors -> CB37 | Read as analog voltages |
| 2 x Rigol DG1022Z function generator | USB (VISA) | `pyvisa` + NI-VISA Runtime backend |
| INFICON VGC083 vacuum gauge | RS-232 (USB-serial adapter) | `pyserial` + adapter driver |
| Agilent XGS-600 vacuum gauge | RS-232 (USB-serial adapter) | `pyserial` + adapter driver |
| Tektronix TDS 2012 oscilloscope | RS-232 (USB-serial adapter) | `pyserial` + adapter driver |
| USB camera | USB (UVC) | Built-in Windows UVC driver |

### LabJack T7 analog input map

There is **one** T7. `MainWindow` owns a single connection and a single poll thread
that reads all 12 channels in one round trip at 10 Hz. Tabs subscribe and filter.

| AIN | Where | Signal | Conversion |
|---|---|---|---|
| AIN0-AIN3 | T7 body terminals | NEC log amps, slits X+/X-/Y+/Y- | log-amp curve -> A |
| AIN4, AIN5 | CB37 | *spare* | - |
| AIN6 / AIN7 | CB37 | Amp **Y-** current / voltage monitor | 1 V = 10 mA / 1 V = 1 kV |
| AIN8 / AIN9 | CB37 | Amp **Y+** current / voltage monitor | " |
| AIN10 / AIN11 | CB37 | Amp **X-** current / voltage monitor | " |
| AIN12 / AIN13 | CB37 | Amp **X+** current / voltage monitor | " |

**Wiring notes.**
CB37 AIN0-AIN3 are electrically duplicated with the T7's own screw terminals - the log
amps use the body terminals, so **nothing may be landed on CB37 AIN0-AIN3.**
Land the eight amplifier BNC shields on **AGND (DB37 pin 30)**, not GND: GND carries
load current and will offset the ADC reference.
All twelve channels run at +/-10 V single-ended. This is required, not merely
convenient - the EEL5000 current monitor reaches +/-10 V during its rated 100 mA / 4 ms
transient.

**Sampling caveat.** At 10 Hz these monitors report a time-average of a kHz-rate
deflection waveform, not its peak. The HV Amplifiers tab is a DC-bias / drift / fault
monitor, not a waveform capture.

Devices are optional - the Analysis tab and all simulation functions work without any hardware connected.

---

## Dependency bootstrapper (one-click driver install)

The app depends on several system-level libraries that PyInstaller cannot bundle.
On a fresh control PC any of them may be missing, so the app includes a **dependency
bootstrapper** that detects and resolves them automatically at startup.

When RBL launches, `rbl/driver.py` runs a preflight check on each system-level
dependency. If anything is missing, a yellow warning bar appears below the tab bar
listing every issue. When a bundled installer is available for a dependency, an
"Install" button appears next to it. Clicking launches the vendor's own installer
with a Windows UAC admin prompt. After the install finishes, restart RBL.

### What gets checked

| Dependency | What it serves | How the check works |
|---|---|---|
| **LabJack LJM** (`LabJackM.dll`) | T7 analog input (log amps, HV amp monitors) | Imports `labjack.ljm` and reads the library version — proves the DLL is present AND responding |
| **NI-VISA Runtime** | Rigol DG1022Z function generators (USB-TMC) | Creates a `pyvisa.ResourceManager()` — fails if no VISA backend is installed system-wide |
| **USB-serial adapter driver** | VGC083 / XGS-600 vacuum gauges, TDS 2012 oscilloscope | Checks that `pyserial` can enumerate COM ports. Since a missing adapter driver only shows up when the adapter is plugged in (no COM port appears), this is a softer check |

### What is NOT checked (works out of the box)

| Dependency | Why it's fine |
|---|---|
| **Galil DMC-4103** | Pure TCP sockets — no driver beyond Windows networking |
| **USB camera** | Standard UVC driver — built into Windows |

### Setting up bundled installers

Drop installer `.exe` files into the `vendor/` folder at the project root. The
PyInstaller spec file automatically bundles anything in `vendor/*.exe` into the
build. The bootstrapper uses filename patterns to match each installer to the
right dependency:

| Dependency | Filename pattern | Where to download |
|---|---|---|
| LabJack LJM | `LabJack*.exe` | [LabJack LJM installer](https://support.labjack.com/docs/ljm-software-installer-windows) |
| NI-VISA Runtime | `NI-VISA*.exe` or `ni-visa*.exe` | [NI-VISA Runtime](https://www.ni.com/en/support/downloads/drivers/download.ni-visa.html) (get the Runtime, not the full Development suite) |
| USB-serial (FTDI) | `FTDI*.exe` or `CDM*.exe` | [FTDI VCP drivers](https://ftdichip.com/drivers/vcp-drivers/) |
| USB-serial (Prolific) | `PL2303*.exe` | [Prolific PL2303 driver](http://www.prolific.com.tw/US/ShowProduct.aspx?p_id=225&pcid=41) |
| USB-serial (CH340) | `CH34*.exe` | [WCH CH340 driver](http://www.wch-ic.com/downloads/CH341SER_EXE.html) |

You can bundle as many or as few as you need. If no installer is bundled for a
dependency, the warning bar still appears but directs the user to download it
from the vendor's website.

### Caveats

- The UAC admin prompt cannot be coded away — it is a Windows security boundary
  for anything installing system-wide.
- Each bundled installer adds its own size to the build: LabJack LJM ~18 MB,
  NI-VISA Runtime ~100-400 MB (online vs offline installer), USB-serial drivers
  ~5-10 MB each.
- Windows 10/11 auto-installs FTDI and CH340 USB-serial drivers via Windows Update
  in most cases, so you may not need to bundle those at all. Prolific PL2303 is the
  one most likely to need manual installation.

### Reusing this pattern in another project

The dependency bootstrapper is the same technique as a game shipping the Visual C++
runtime (`vcredist`) — detect whether the dependency is present, then delegate to the
vendor's own installer. To reuse it:

1. **Copy `rbl/driver.py`** into your project. It has no GUI code, so it drops in
   anywhere. Rewrite the `check_*()` functions to probe YOUR dependencies (an import,
   a DLL check, a `subprocess` version call — whatever proves the thing is alive).

2. **Wire the GUI.** Add a `_refresh_driver_state()` that calls `check_all()` and
   builds a row for each failure with a label and optional Install button. Call it on
   startup and on connection failure so the actionable message wins over any generic
   error.

3. **Update your `.spec` file.** Add the `_vendor_datas()` helper that globs
   `vendor/*.exe` into the PyInstaller datas list.

4. **Create a `vendor/` folder** and drop the installer `.exe` files into it.

---

## Building

```
cd C:\Users\IGLeg\PycharmProjects\RBL
.venv\Scripts\activate
pyinstaller rbl.spec --clean
```

Or just run `build.bat`, which cleans, builds, and copies `dist/` to `D:\`.

---

## License

MIT - see `LICENSE` file.

---

If you have any questions, comments, or concerns email me - ilegault@wisc.edu
