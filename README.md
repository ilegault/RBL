# RBL

Desktop application for planning, simulating, controlling, and evaluating the right beam line in IBL at UW-Madison NEEP

---

## Requirements

### Hardware

| Device | Interface | Driver |
|---|---|---|
| Galil DMC-4103 motion controller | TCP/IP (port 23) | Built-in ASCII socket protocol |
| LabJack T7 analog input | USB or Ethernet | `labjack-ljm` Python package + LJM system library |

Devices are optional — the Analysis tab and all simulation functions work without any hardware connected

### Ensure you have LabJack LJM library installed on the system


---

## License

MIT — see `LICENSE` file.

---

If you have any questions, comments, or concerns email me - ilegault@wisc.edu
