"""
cup_actuation_link.py
Beamline mixin for Faraday cup actuation and confirmed position feedback (ADR 0003).

WHY THIS EXISTS
---------------
ADR 0003: The Right Beam Line's Faraday cup has an ESM remote connector operated
via a LabJack T7 and an LJTick-RelayDriver (LJTRD) with relay isolation.
The application can command the cup out and back, and reads back confirmed position
from the controller's isolated status contacts (FIO2 = IN, FIO3 = OUT, FIO4 = AUTO).

COMMANDED VS CONFIRMED POSITION
-------------------------------
Commanded position (what the application requested) and confirmed position
(what the controller's status contacts report) are distinct quantities separated
by a physical mechanical lag. Only confirmed position opens and closes acquisition
runs. The two are kept strictly apart in snapshots and never collapsed.

FAIL-SAFE INTO THE BEAM & DRIVE RELEASE
---------------------------------------
Cup OUT requires two contact closures:
- Relay 1 (enable OUT control, FIO0)
- Relay 2 (command OUT, FIO1)
Any loss of drive opens both relays, and the mechanical pneumatic cylinder returns
the cup IN, intercepting the beam ahead of the specimen. This is a property of the
wiring and no software path is load-bearing for it.

Relay 1 is held closed (1) while the application is connected and in control, and
opened (0) on disconnect and on application shutdown, as an explicit release of
control. Relay 2 (command OUT) is the one that cycles.

Shutdown de-asserts both digital output lines (releasing drive) — it NEVER issues
a positional cup command.

PER-SCAN TRANSITION TIMESTAMPS AND CONTACT DEBOUNCE
---------------------------------------------------
FIO_STATE transitions arrive in the FULL stream window with exact scan indices.
Contact bounce is filtered using CUP_CONTACT_DEBOUNCE_S (0.05 s = 50 ms): a new
candidate position must be sustained continuously across scans for at least 50 ms
before confirming. The timestamp of the confirmed transition is the exact sample
timestamp when the transition began, derived from window t, sample_period, and scan index.

STALENESS VS DISCONNECT
-----------------------
Staleness means FIO_STATE was absent or None in the last window — which is what
happens in every profile but FULL — or no window has arrived within a stale threshold.
It is distinct from disconnected and distinguishable from it in the snapshot.
"""
from __future__ import annotations

import math

from rbl.config.cup_config import (
    CUP_COMMAND_OUT_LINE,
    CUP_CONTACT_DEBOUNCE_S,
    CUP_ENABLE_LINE,
)
from rbl.hardware.cup_status import CupPosition, decode_cup_status
from rbl.snapshots import CupActuationState


class CupActuationLinkMixin:
    """Faraday cup actuation command surface and confirmed status ingestion.

    Mixed into Beamline. Requires host class to declare:
    - cup_actuation_changed = Signal(object)
    - cup_position_changed  = Signal(object)  (alias)
    """

    def _init_cup_actuation(self) -> None:
        """Called from Beamline.__init__."""
        self._cup_actuation_connected: bool = False
        self._cup_commanded: CupPosition = CupPosition.IN
        self._cup_confirmed: CupPosition = CupPosition.IN_TRANSIT
        self._cup_auto_mode: bool = False
        self._cup_stale: bool = False
        self._cup_last_transition_t: float = float("nan")
        self._cup_last_window_t: float = float("nan")

        # Per-scan debounce state tracking
        self._cup_candidate_position: CupPosition | None = None
        self._cup_candidate_start_t: float = float("nan")
        self._cup_candidate_last_t: float = float("nan")

    def _publish_cup_actuation_snapshot(self) -> CupActuationState:
        """Construct and emit a typed CupActuationState snapshot."""
        lj = getattr(self, "lj", None)
        lj_conn = bool(lj is not None and getattr(lj, "connected", False))
        connected = lj_conn or self._cup_actuation_connected
        snapshot = CupActuationState(
            connected=connected,
            commanded=self._cup_commanded,
            confirmed=self._cup_confirmed,
            auto_mode=self._cup_auto_mode,
            stale=self._cup_stale,
            last_transition_t=self._cup_last_transition_t,
            t=self._cup_last_window_t,
        )
        if hasattr(self, "cup_actuation_changed"):
            self.cup_actuation_changed.emit(snapshot)
        if hasattr(self, "cup_position_changed"):
            self.cup_position_changed.emit(snapshot)
        return snapshot

    def command_cup(self, position: CupPosition | str) -> None:
        """Command the cup to a position (CupPosition.IN or CupPosition.OUT).

        Relay 1 (enable) is held closed (1).
        Relay 2 (command OUT) is set to 1 for OUT, or 0 for IN.
        Immediately publishes a snapshot with the updated commanded position.
        """
        if isinstance(position, str):
            position = CupPosition(position)
        if position not in (CupPosition.IN, CupPosition.OUT):
            raise ValueError(
                f"Cannot command cup to transitional position '{position}'; must be IN or OUT."
            )

        self._cup_commanded = position
        if position == CupPosition.OUT:
            self._write_cup_digital(CUP_ENABLE_LINE, 1)
            self._write_cup_digital(CUP_COMMAND_OUT_LINE, 1)
        else:
            self._write_cup_digital(CUP_ENABLE_LINE, 1)
            self._write_cup_digital(CUP_COMMAND_OUT_LINE, 0)

        self._publish_cup_actuation_snapshot()

    def command_cup_in(self) -> None:
        """Helper to command cup IN (into the beam path)."""
        self.command_cup(CupPosition.IN)

    def command_cup_out(self) -> None:
        """Helper to command cup OUT (retracted from the beam path)."""
        self.command_cup(CupPosition.OUT)

    def _write_cup_digital(self, line: str, val: int) -> None:
        """Write digital output line via stream worker queue or direct T7 handle."""
        worker = getattr(self, "_lj_worker", None)
        if worker is not None and hasattr(worker, "isRunning") and worker.isRunning():
            worker.queue_digital_write(line, val)
        else:
            lj = getattr(self, "lj", None)
            if lj is not None and getattr(lj, "connected", False):
                try:
                    lj.write_digital(line, val)
                except Exception:
                    pass

    def _release_cup_drive(self) -> None:
        """De-assert both digital output relays, releasing coil power (failsafe into beam).

        Does NOT command a position through command_cup(); direct release of drive.
        """
        self._write_cup_digital(CUP_COMMAND_OUT_LINE, 0)
        self._write_cup_digital(CUP_ENABLE_LINE, 0)

    def _on_labjack_connect_cup(self) -> None:
        """Called when LabJack T7 connects."""
        self._cup_actuation_connected = True
        self._cup_commanded = CupPosition.IN
        self._cup_stale = False
        # Energise Relay 1 (enable), de-assert Relay 2 (command OUT)
        self._write_cup_digital(CUP_ENABLE_LINE, 1)
        self._write_cup_digital(CUP_COMMAND_OUT_LINE, 0)
        self._publish_cup_actuation_snapshot()

    def _on_labjack_disconnect_cup(self) -> None:
        """Called when LabJack T7 disconnects."""
        self._cup_actuation_connected = False
        self._release_cup_drive()
        self._publish_cup_actuation_snapshot()

    def ingest_cup_actuation_window(self, payload: dict) -> None:
        """Ingest a stream window payload from LabJackStreamWorker."""
        t_window = payload.get("t", float("nan"))
        self._cup_last_window_t = t_window
        channels = payload.get("channels", {})
        fio_entry = channels.get("FIO_STATE")
        if fio_entry is None:
            # FIO_STATE is absent in non-FULL profiles -> marked stale
            self._cup_stale = True
            self._publish_cup_actuation_snapshot()
            return

        self._cup_stale = False

        # Extract timing
        window_samples = payload.get("window_samples") or 750
        sample_period = payload.get("sample_period") or (1.0 / 7500.0)

        # Parse FIO_STATE entry
        first_val: int
        transitions: list[tuple[int, int]]

        if isinstance(fio_entry, int):
            first_val = fio_entry
            transitions = []
        elif isinstance(fio_entry, dict):
            first_val = int(fio_entry.get("first", 0))
            transitions = list(fio_entry.get("transitions", []))
        else:
            first_val = 0
            transitions = []

        # Build segments of (scan_start, scan_end, raw_val)
        segments: list[tuple[int, int, int]] = []
        cur_start = 0
        cur_val = first_val

        for trans_idx, trans_val in transitions:
            if trans_idx > cur_start:
                segments.append((cur_start, trans_idx - 1, cur_val))
            cur_start = trans_idx
            cur_val = trans_val

        segments.append((cur_start, window_samples - 1, cur_val))

        # Process segments in time order
        for s_start, s_end, val in segments:
            # Exact sample timestamp for scan i: t_window - (window_samples - 1 - i) * sample_period
            t_start = t_window - (window_samples - 1 - s_start) * sample_period
            t_end = t_window - (window_samples - 1 - s_end) * sample_period + sample_period

            status = decode_cup_status(val)
            self._cup_auto_mode = status.auto_mode
            pos = status.position

            # Candidate debounce tracking across scans and windows
            is_contiguous = (
                s_start == 0
                and not math.isnan(self._cup_candidate_last_t)
                and abs(t_start - self._cup_candidate_last_t) <= sample_period * 2.5
            )

            if pos != self._cup_candidate_position or (s_start == 0 and not is_contiguous):
                self._cup_candidate_position = pos
                self._cup_candidate_start_t = t_start
                self._cup_candidate_last_t = t_end
            else:
                self._cup_candidate_last_t = t_end

            duration = self._cup_candidate_last_t - self._cup_candidate_start_t
            if duration >= CUP_CONTACT_DEBOUNCE_S:
                if self._cup_confirmed != pos:
                    self._cup_confirmed = pos
                    self._cup_last_transition_t = self._cup_candidate_start_t

        self._publish_cup_actuation_snapshot()
