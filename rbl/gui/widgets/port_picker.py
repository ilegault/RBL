"""
port_picker.py
The COM-port control every serial instrument in this app uses: a dropdown of
the ports that actually exist, a Refresh, and a Detect that asks each port
what it is.

WHY A DROPDOWN AND NOT A TEXT FIELD
-----------------------------------
A typed port name is a guess. COM numbering moves when a USB adapter is
unplugged and replugged into a different socket, so the number that worked
last week names a different adapter today - and a wrong name fails as a
timeout, which reads like a dead instrument rather than a wrong port. The
dropdown can only offer ports that exist, and each is shown with the
adapter's own description so an operator can tell two of them apart.

WHY DETECT RUNS OFF THE GUI THREAD
----------------------------------
Probing opens every port and waits for an identity reply at each candidate
baud. With several adapters present that is seconds of blocking, and a
blocked Qt event loop is an application that has visibly stopped responding
- during a run, that reads as a crash. PortScanWorker does it on its own
thread and reports back.

DETECT DOES NOT CONNECT
-----------------------
It selects what it found and stops there. Connecting is a separate,
deliberate press, because a scan is something an operator runs to find out
what is plugged in - not necessarily an instruction to start driving it.
"""
import logging

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from rbl.gui import theme
from rbl.gui.widgets.inputs import NoScrollComboBox

log = logging.getLogger(__name__)

AUTO = None          # the dropdown's "Auto-detect" entry carries this


class PortScanWorker(QThread):
    """Probes serial ports for one or more instruments, off the GUI thread."""

    scan_done = Signal(dict)      # {"xgs600": "COM4", ...}; empty if none

    def __init__(self, keys, parent=None):
        super().__init__(parent)
        self._keys = list(keys)

    def run(self):
        found = {}
        try:
            from rbl.hardware.serial_transport import (
                candidates_for, discover, load_saved_ports, save_ports,
            )
            found = discover(candidates=candidates_for(self._keys),
                             saved=load_saved_ports())
            if found:
                save_ports(found)
        except Exception:
            log.exception("port_picker: scan for %s failed", self._keys)
        self.scan_done.emit(found)


class PortPicker(QWidget):
    """Port dropdown + Refresh + Detect for one instrument.

    Parameters
    ----------
    instrument_key : the serial_transport candidate key ("tds2012",
                     "xgs600", "vgc083") this picker detects.  This is what
                     the probe matches on - a display string here means the
                     probe looks for an instrument that does not exist and
                     always reports "not found".
    label          : text before the dropdown; "" for none.
    display_name   : what to call the instrument in messages; defaults to
                     the key.
    show_auto      : include an "Auto-detect" entry whose value is None.
    """

    port_changed = Signal(object)     # port string, or None for auto
    detected     = Signal(object)     # port string, or None if nothing found
    status       = Signal(str, str)   # (message, theme colour)

    def __init__(self, instrument_key: str, label: str = "Port:",
                 show_auto: bool = True, display_name: str = "",
                 parent=None):
        super().__init__(parent)
        # instrument_key must be a serial_transport candidate key, not a
        # display string: it is what the probe matches on, and an unknown
        # key silently probes for nothing and reports "not found".
        self._key = instrument_key
        self._name = display_name or instrument_key
        self._show_auto = show_auto
        self._worker = None

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        if label:
            lay.addWidget(QLabel(label))

        self._combo = NoScrollComboBox()
        self._combo.setMinimumWidth(260)
        self._combo.setToolTip(
            "Serial ports on this machine, with each adapter's own "
            "description. COM numbers move when an adapter is replugged, so "
            "use Detect rather than remembering a number.")
        self._combo.currentIndexChanged.connect(
            lambda _i: self.port_changed.emit(self.current_port()))
        lay.addWidget(self._combo)

        self._btn_refresh = QPushButton("Refresh")
        self._btn_refresh.setToolTip("Re-read the list of serial ports.")
        self._btn_refresh.clicked.connect(self.refresh)
        lay.addWidget(self._btn_refresh)

        self._btn_detect = QPushButton("Detect")
        self._btn_detect.setToolTip(
            f"Ask each port what it is and select the one that answers as "
            f"the {self._name}. Takes a few seconds; the saved port is "
            f"tried first. Does not connect.")
        self._btn_detect.clicked.connect(self.detect)
        lay.addWidget(self._btn_detect)

        self.refresh()

    # -----------------------------------------------------------------------

    def refresh(self):
        """Repopulate the dropdown, keeping the current selection if it lives."""
        previous = self.current_port() if self._combo.count() else None
        self._combo.blockSignals(True)
        self._combo.clear()
        if self._show_auto:
            self._combo.addItem("Auto-detect", AUTO)
        try:
            from rbl.hardware.serial_transport import enumerate_ports
            ports = enumerate_ports()
        except Exception:
            log.exception("port_picker: could not enumerate serial ports")
            ports = []
        for info in ports:
            label = (f"{info.device} — {info.description}"
                     if info.description else info.device)
            self._combo.addItem(label, info.device)
        if previous is not None:
            idx = self._combo.findData(previous)
            if idx < 0:
                # The selection is not in the enumerated list - a saved port
                # whose adapter is unplugged, or one a scan reported. Keep
                # it rather than silently falling back to Auto-detect: a
                # selection that changes itself is how you end up connecting
                # to the wrong instrument without having chosen to.
                self._combo.addItem(f"{previous} (not present)", previous)
                idx = self._combo.count() - 1
            self._combo.setCurrentIndex(idx)
        self._combo.blockSignals(False)
        if not ports:
            self.status.emit("no serial ports found", theme.WARN)

    def detect(self):
        """Probe for this instrument in the background, then select it."""
        if self._worker is not None:
            return
        self._btn_detect.setEnabled(False)
        self._btn_detect.setText("Detecting…")
        self.status.emit(f"probing serial ports for the {self._name}…",
                         theme.NEUTRAL)
        self._worker = PortScanWorker([self._key], self)
        self._worker.scan_done.connect(self._on_scan_done)
        self._worker.start()

    def _on_scan_done(self, found: dict):
        self._btn_detect.setEnabled(True)
        self._btn_detect.setText("Detect")
        self._worker = None
        port = found.get(self._key)
        self.refresh()
        if port:
            self.set_port(port)
            self.status.emit(f"{self._name} found on {port}", theme.OK)
        else:
            self.status.emit(
                f"no {self._name} answered — check the cable, and that the "
                f"instrument's baud matches", theme.WARN)
        self.detected.emit(port)

    # -----------------------------------------------------------------------

    def current_port(self):
        """Selected port string, or None when 'Auto-detect' is chosen."""
        return self._combo.currentData() if self._combo.count() else None

    def set_port(self, port: str):
        """Select *port*, adding it to the list if it is not there."""
        if not port:
            if self._show_auto:
                self._combo.setCurrentIndex(0)
            return
        idx = self._combo.findData(port)
        if idx < 0:
            self._combo.addItem(port, port)
            idx = self._combo.count() - 1
        self._combo.setCurrentIndex(idx)

    def set_busy(self, busy: bool):
        """Grey the controls out while a connection attempt is in flight."""
        self._combo.setEnabled(not busy)
        self._btn_refresh.setEnabled(not busy)
        self._btn_detect.setEnabled(not busy)
