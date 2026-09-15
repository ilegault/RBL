"""
faraday_cup_tab.py
PySide6 widget for the "Faraday Cup" outer tab.

Renders the live Faraday cup current from the Keithley 6482 dual-channel
picoammeter (Channel 1).

WHY THIS EXISTS
---------------
The beamline's four NEC log amps measure slit current — the beam intercepted by
the slit jaws on the way through. The Faraday cup measures transmitted current —
the charge that reaches the target / sample location.

This tab displays the live cup current as a first-class quantity, auto-scaled
from nanoamps to milliamps, and provides a dedicated connection panel to connect
and disconnect the picoammeter independently.

ONE INSTRUMENT, ONE OWNER
-------------------------
This tab owns no driver, opens no VISA session, and spawns no threads. Beamline
(rbl/state/picoammeter_link.py) is the sole owner of the instrument handle and
publishes CupState snapshots.

NO UNIT CONVERSION
------------------
The Keithley 6482 returns current already in Amperes. The tab receives the
snapshot and auto-scales the display for human readability via format_current(),
without altering or converting the physical value.

STALE DATA & DISCONNECT
-----------------------
With nothing connected, the tab clearly displays a disconnected status and
placeholder ("—"), never displaying zero or a stale measurement.
Over-range conditions are shown explicitly as "OVER-RANGE" rather than numbers.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from rbl.config.cup_config import KEITHLEY_6482_DEFAULT_RESOURCE
from rbl.gui import theme
from rbl.gui.widgets.connection_bar import StatusPill
from rbl.hardware.current_monitor import format_current
from rbl.services.cup_acquisition import CupAcquisitionStateMachine
from rbl.snapshots import CupState

if TYPE_CHECKING:
    from rbl.state.beamline import Beamline

log = logging.getLogger(__name__)


class FaradayCupTab(QWidget):
    """The 'Faraday Cup' outer tab."""

    def __init__(self, beamline: Beamline | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.beamline = beamline
        self._connected = False
        self.acquisition = CupAcquisitionStateMachine()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # ── Connection Panel ──────────────────────────────────────────────────
        conn_box = QGroupBox("Keithley 6482 Picoammeter")
        conn_lay = QHBoxLayout(conn_box)

        conn_lay.addWidget(QLabel("VISA Resource:"))
        self.le_resource = QLineEdit(KEITHLEY_6482_DEFAULT_RESOURCE)
        self.le_resource.setMaximumWidth(220)
        self.le_resource.setToolTip(
            "VISA resource identifier for the Keithley 6482 (e.g. GPIB0::14::INSTR)"
        )
        conn_lay.addWidget(self.le_resource)

        self.btn_connect = QPushButton("Connect")
        self.btn_connect.setStyleSheet("font-weight: bold; padding: 4px 12px;")
        self.btn_connect.clicked.connect(self._on_connect_clicked)
        conn_lay.addWidget(self.btn_connect)

        self.status_pill = StatusPill()
        conn_lay.addWidget(self.status_pill)

        self.lbl_ident = QLabel("")
        self.lbl_ident.setStyleSheet(f"color: {theme.NEUTRAL}; font-style: italic;")
        conn_lay.addWidget(self.lbl_ident, stretch=1)

        layout.addWidget(conn_box)

        # ── Live Reading Panel ────────────────────────────────────────────────
        reading_box = QGroupBox("Live Cup Current")
        reading_lay = QVBoxLayout(reading_box)
        reading_lay.setSpacing(6)
        reading_lay.setContentsMargins(12, 10, 12, 10)

        header_lay = QHBoxLayout()
        lbl_channel = QLabel("Faraday Cup  (Channel 1)")
        lbl_channel.setStyleSheet(
            f"font-size: {theme.FS_BIG}px; color: {theme.NEUTRAL}; font-weight: bold;"
        )
        header_lay.addWidget(lbl_channel)
        header_lay.addStretch()
        reading_lay.addLayout(header_lay)

        mono_font = QFont("Consolas", 24)
        mono_font.setBold(True)

        self.lbl_current = QLabel("  —    ")
        self.lbl_current.setFont(mono_font)
        self.lbl_current.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_current.setStyleSheet(theme.status_label(theme.MUTED))
        reading_lay.addWidget(self.lbl_current)

        detail_lay = QHBoxLayout()
        self.lbl_detail = QLabel("Not connected")
        self.lbl_detail.setStyleSheet(
            f"color: {theme.MUTED}; font-style: italic; font-size: {theme.FS_LABEL}px;"
        )
        detail_lay.addWidget(self.lbl_detail)
        detail_lay.addStretch()

        self.lbl_meta = QLabel("")
        self.lbl_meta.setStyleSheet(
            f"color: {theme.MUTED}; font-family: Consolas, 'Courier New', monospace; "
            f"font-size: {theme.FS_TINY}px;"
        )
        detail_lay.addWidget(self.lbl_meta)
        reading_lay.addLayout(detail_lay)

        layout.addWidget(reading_box)

        # ── Acquisition Run Panel ─────────────────────────────────────────────
        acq_box = QGroupBox("Acquisition Run")
        acq_lay = QHBoxLayout(acq_box)
        acq_lay.setSpacing(10)
        acq_lay.setContentsMargins(12, 10, 12, 10)

        acq_lay.addWidget(QLabel("Run Status:"))
        self.lbl_run_status = QLabel("Idle")
        self.lbl_run_status.setStyleSheet(theme.status_label(theme.MUTED))
        self.lbl_run_status.setMinimumWidth(160)
        acq_lay.addWidget(self.lbl_run_status)

        acq_lay.addSpacing(20)

        self.btn_force_start = QPushButton("Force Start")
        self.btn_force_start.setStyleSheet("font-weight: bold; padding: 4px 12px;")
        self.btn_force_start.setToolTip(
            "Force an acquisition run to begin immediately regardless of current"
        )
        self.btn_force_start.clicked.connect(self._on_force_start_clicked)
        acq_lay.addWidget(self.btn_force_start)

        self.btn_force_stop = QPushButton("Force Stop")
        self.btn_force_stop.setStyleSheet("font-weight: bold; padding: 4px 12px;")
        self.btn_force_stop.setToolTip(
            "Force the active acquisition run to stop immediately regardless of current"
        )
        self.btn_force_stop.clicked.connect(self._on_force_stop_clicked)
        acq_lay.addWidget(self.btn_force_stop)

        acq_lay.addStretch(1)

        layout.addWidget(acq_box)
        layout.addStretch(1)

        # Set initial disconnected state
        self._set_disconnected_view()

    # ── Connection Handling ───────────────────────────────────────────────────

    def get_resource(self) -> str:
        """Return configured VISA resource string."""
        return self.le_resource.text().strip() or KEITHLEY_6482_DEFAULT_RESOURCE

    def connect_if_needed(self) -> tuple[str, str]:
        """Connect picoammeter if not already connected."""
        if self.beamline is None:
            return "failed", "No beamline attached"
        if self.beamline.picoammeter_connected:
            return "already", "Faraday cup picoammeter already connected"
        try:
            res = self.get_resource()
            self.beamline.connect_picoammeter(res)
            return "connected", f"Faraday cup picoammeter ({res})"
        except Exception as exc:
            return "failed", f"Picoammeter: {exc}"

    def _on_connect_clicked(self) -> None:
        if self.beamline is None:
            return
        if self._connected:
            self.beamline.disconnect_picoammeter()
        else:
            resource = self.get_resource()
            try:
                self.beamline.connect_picoammeter(resource)
            except Exception as exc:
                log.exception("FaradayCupTab connect failed: %s", exc)

    def _on_force_start_clicked(self) -> None:
        transition = self.acquisition.force_start(t=time.time())
        if transition is not None and self.beamline is not None:
            self.beamline.set_cup_acquiring(True)
        self._update_acquisition_view()

    def _on_force_stop_clicked(self) -> None:
        transition = self.acquisition.force_stop(t=time.time())
        if transition is not None and self.beamline is not None:
            self.beamline.set_cup_acquiring(False)
        self._update_acquisition_view()

    def _update_acquisition_view(self) -> None:
        if not self._connected:
            self.lbl_run_status.setText("Disconnected")
            self.lbl_run_status.setStyleSheet(theme.status_label(theme.MUTED))
            self.btn_force_start.setEnabled(False)
            self.btn_force_stop.setEnabled(False)
            return

        if self.acquisition.is_acquiring:
            run_id = self.acquisition.current_run_id
            run_lbl = f"ACQUIRING (Run #{run_id})" if run_id else "ACQUIRING"
            self.lbl_run_status.setText(run_lbl)
            self.lbl_run_status.setStyleSheet(theme.status_label(theme.OK))
            self.btn_force_start.setEnabled(False)
            self.btn_force_stop.setEnabled(True)
        else:
            if self.acquisition.cup_in_beam:
                self.lbl_run_status.setText("In Beam (Arming)")
                self.lbl_run_status.setStyleSheet(theme.status_label(theme.WARN))
            else:
                self.lbl_run_status.setText("Idle")
                self.lbl_run_status.setStyleSheet(theme.status_label(theme.MUTED))
            self.btn_force_start.setEnabled(True)
            self.btn_force_stop.setEnabled(False)

    def _set_disconnected_view(self) -> None:
        self._connected = False
        self.status_pill.set_connected(False)
        self.btn_connect.setText("Connect")
        self.lbl_current.setText("  —    ")
        self.lbl_current.setStyleSheet(theme.status_label(theme.MUTED))
        self.lbl_detail.setText("Not connected")
        self.lbl_detail.setStyleSheet(f"color: {theme.MUTED}; font-style: italic;")
        self.lbl_ident.setText("")
        self.lbl_meta.setText("")
        self.acquisition.disconnect(t=time.time())
        if self.beamline is not None:
            self.beamline.set_cup_acquiring(False)
        self._update_acquisition_view()

    # ── Snapshot / State Updates ──────────────────────────────────────────────

    def on_cup_state(self, state: CupState) -> None:
        """Render a CupState snapshot published by Beamline."""
        if not state.connected:
            self._set_disconnected_view()
            return

        self._connected = True
        self.status_pill.set_connected(True)
        self.btn_connect.setText("Disconnect")

        # Update acquisition state machine
        t_sample = state.t_host if state.t_host == state.t_host else time.time()
        transition = self.acquisition.update(
            current=state.current,
            t=t_sample,
            over_range=state.over_range,
            connected=True,
        )
        if transition is not None and self.beamline is not None:
            self.beamline.set_cup_acquiring(self.acquisition.is_acquiring)

        self._update_acquisition_view()

        if state.over_range:
            self.lbl_current.setText("OVER-RANGE")
            self.lbl_current.setStyleSheet(theme.status_label(theme.FAULT))
            self.lbl_detail.setText("Over-range detected")
            self.lbl_detail.setStyleSheet(f"color: {theme.FAULT}; font-style: italic;")
        elif state.unavailable or state.current is None:
            self.lbl_current.setText("  —    ")
            self.lbl_current.setStyleSheet(theme.status_label(theme.WARN))
            self.lbl_detail.setText("Reading unavailable")
            self.lbl_detail.setStyleSheet(f"color: {theme.WARN}; font-style: italic;")
        else:
            self.lbl_current.setText(format_current(state.current))
            self.lbl_current.setStyleSheet(theme.status_label(theme.OK))
            self.lbl_detail.setText("Reading OK")
            self.lbl_detail.setStyleSheet(f"color: {theme.NEUTRAL}; font-style: italic;")

        meta_parts: list[str] = []
        if state.status_word:
            meta_parts.append(f"Status: 0x{state.status_word:08X}")
        if state.timestamp == state.timestamp:  # not NaN check
            meta_parts.append(f"Inst time: {state.timestamp:.3f} s")
        self.lbl_meta.setText("  |  ".join(meta_parts))

    def on_cup_connected(self, ident: str) -> None:
        """Called when Keithley 6482 connects successfully."""
        self._connected = True
        self.status_pill.set_connected(True)
        self.btn_connect.setText("Disconnect")
        self.lbl_ident.setText(f"{ident}" if ident else "Keithley 6482")
        self.btn_force_start.setEnabled(True)
        self.btn_force_stop.setEnabled(False)

    def on_cup_disconnected(self) -> None:
        """Called when Keithley 6482 disconnects."""
        self._set_disconnected_view()

    def on_cup_error(self, msg: str) -> None:
        """Called when a worker/communication error occurs."""
        log.warning("FaradayCupTab error: %s", msg)
        self.lbl_detail.setText(f"Error: {msg}")
        self.lbl_detail.setStyleSheet(f"color: {theme.FAULT}; font-style: italic;")

