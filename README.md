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
| 4 × EEL5000.20.100 HV amplifier | BNC monitors → CB37 | Read as analog voltages |
| 2 × Rigol DG1022Z function generator | USB (VISA) | `pyvisa` + IVI/NI VISA backend |

### LabJack T7 analog input map

There is **one** T7. `MainWindow` owns a single connection and a single poll thread
that reads all 12 channels in one round trip at 10 Hz. Tabs subscribe and filter.

| AIN | Where | Signal | Conversion |
|---|---|---|---|
| AIN0–AIN3 | T7 body terminals | NEC log amps, jaws X+/X−/Y+/Y− | log-amp curve → A |
| AIN4 / AIN5 | CB37 | Amp **X+** voltage / current monitor | 1 V = 1 kV / 1 V = 10 mA |
| AIN6 / AIN7 | CB37 | Amp **X−** voltage / current monitor | " |
| AIN8 / AIN9 | CB37 | Amp **Y+** voltage / current monitor | " |
| AIN10 / AIN11 | CB37 | Amp **Y−** voltage / current monitor | " |
| AIN12, AIN13 | CB37 | *spare* | — |

**Wiring notes.**
CB37 AIN0–AIN3 are electrically duplicated with the T7's own screw terminals — the log
amps use the body terminals, so **nothing may be landed on CB37 AIN0–AIN3.**
Land the eight amplifier BNC shields on **AGND (DB37 pin 30)**, not GND: GND carries
load current and will offset the ADC reference.
All twelve channels run at ±10 V single-ended. This is required, not merely
convenient — the EEL5000 current monitor reaches ±10 V during its rated 100 mA / 4 ms
transient.

**Sampling caveat.** At 10 Hz these monitors report a time-average of a kHz-rate
deflection waveform, not its peak. The HV Amplifiers tab is a DC-bias / drift / fault
monitor, not a waveform capture.

Devices are optional — the Analysis tab and all simulation functions work without any hardware connected

### Ensure you have LabJack LJM library installed on the system


---

## License

MIT — see `LICENSE` file.

---

If you have any questions, comments, or concerns email me - ilegault@wisc.edu
