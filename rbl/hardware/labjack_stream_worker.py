"""
labjack_stream_worker.py
LabJack T7 stream reader thread.

Drains eStreamRead on its own thread so the GUI thread is never blocked.
All LJM stream calls (eStreamStart, eStreamRead, eStreamStop) happen here;
the owner (MainWindow) must never call them from the GUI thread.

Profile awareness
-----------------
Each worker instance is bound to a single profile ("WAVEFORM" or "FULL")
and to a single LJM handle received from LabJackT7.  The worker does NOT
call openS — single-handle ownership stays with LabJackT7.

To switch profiles the owner must:
    1. Call worker.stop() and wait(ms) until the thread exits.
    2. Construct a new LabJackStreamWorker with the new profile name.
    3. Connect signals and call start().

This stop/reconfigure/start cycle is the ONLY supported way to change the
scan list.  The T7 does not allow mid-stream scan-list modifications — this
is a hardware constraint, not a style choice.

window_ready payload
--------------------
Emitted once per GUI refresh window (GUI_REFRESH_HZ = 10 Hz).

    {
      "profile":        str   — active profile name
      "window_samples": int   — samples per channel in this window
      "t":              float — monotonic elapsed seconds since worker start
      "channels": {
          "AIN6":  {"waveform": np.ndarray,   # raw volts
                    "peak":     float,         # max(|waveform|) in volts
                    "pk_pk":    float,         # max - min in volts
                    "rms":      float},        # RMS in volts
          ...                                  # (one entry per amp channel)
          "AIN0":  {"mean": float},            # mean volts (FULL profile only)
          ...                                  # (one entry per log-amp channel)
          "AIN0":  None,                       # absent from WAVEFORM profile
          ...
      }
    }

Log-amp channels absent from the active scan list receive a None entry so
consumers can show a "paused" state rather than displaying stale numbers.
"""

import time
import numpy as np
from PySide6.QtCore import QThread, Signal

try:
    from labjack import ljm as _ljm
    _LJM_AVAILABLE = True
except Exception:
    _ljm = None
    _LJM_AVAILABLE = False

from rbl.config.labjack_stream_config import (
    STREAM_PROFILES, STREAM_RANGE_VOLTS,
    AMP_CHANNELS, LOGAMP_CHANNELS, DEFAULT_PROFILE, window_samples,
    resolution_index, is_single_channel, channel_choices,
)


def _ain_address(ain_name: str) -> int:
    """Return the Modbus start address for an AIN channel name.

    From the T7 register map: AINs are 32-bit floats (2 Modbus registers each).
    AIN{n} starts at address n*2.  Valid for n in 0..13.

    Example: AIN0 -> 0, AIN6 -> 12, AIN13 -> 26.
    """
    return int(ain_name[3:]) * 2


class LabJackStreamWorker(QThread):
    """Reads T7 stream data, windows it, and emits one payload per GUI frame.

    See module docstring for payload format and profile-switching rules.
    """

    window_ready = Signal(dict)
    error        = Signal(str)

    def __init__(self, handle, profile_name: str = DEFAULT_PROFILE,
                 channel_override: str = None, parent=None):
        """
        Parameters
        ----------
        handle           : int
            Open LJM handle from LabJackT7.handle.  Must remain valid for the
            lifetime of this worker.  Do NOT close it here.
        profile_name     : str
            One of the keys in STREAM_PROFILES.
        channel_override : str, optional
            For single-channel profiles ("SINGLE_FAST" / "SINGLE_HIRES"), the
            AIN name to stream (e.g. "AIN7").  Must be one of the profile's
            channel_choices.  Ignored for multi-channel profiles, whose scan
            list is fixed.  If None on a single-channel profile, the profile's
            default scan_list channel is used.
        """
        super().__init__(parent)
        self._handle           = handle
        self._profile_name     = profile_name
        self._channel_override = channel_override
        self._running          = True
        self._stream_active    = False

    def stop(self):
        """Signal the read loop to exit.  Call wait(ms) afterwards."""
        self._running = False

    # ------------------------------------------------------------------
    # QThread entry point — runs on the worker thread
    # ------------------------------------------------------------------

    def run(self):
        if not _LJM_AVAILABLE:
            self.error.emit(
                "labjack-ljm is not importable — stream mode unavailable. "
                "Install: pip install labjack-ljm  (also needs LJM system library)"
            )
            return

        profile        = STREAM_PROFILES[self._profile_name]
        # scan_names: ascending physical AIN order (see rbl/config/labjack_stream_config.py)
        # For single-channel profiles the scan list is a single user-selected
        # channel; for multi-channel profiles it is the profile's fixed list.
        if is_single_channel(self._profile_name):
            choices = channel_choices(self._profile_name)
            ch      = self._channel_override or profile["scan_list"][0]
            if ch not in choices:
                self.error.emit(
                    f"[{self._profile_name}] channel '{ch}' is not a valid "
                    f"single-channel target (choices: {choices})."
                )
                return
            scan_names = [ch]
        else:
            scan_names = list(profile["scan_list"])
        scan_rate      = profile["per_channel_rate_hz"]
        res_index      = resolution_index(self._profile_name)
        n_ch           = len(scan_names)
        scans_per_read = window_samples(self._profile_name)

        # AIN name -> Modbus address.  Computed once; stride = n_ch in the
        # de-interleave reshape below.  If this list and n_ch ever disagree,
        # the assert inside the read loop will catch it immediately.
        scan_addresses = [_ain_address(ch) for ch in scan_names]

        t0 = time.monotonic()

        try:
            # --- Configure each AIN in the active scan list -------------------
            # Range and single-ended ground must be set per channel.
            # STREAM_RESOLUTION_INDEX is a single global T7 register (not per-
            # channel in stream mode); it must be written before eStreamStart.
            for ch in scan_names:
                _ljm.eWriteName(self._handle, f"{ch}_RANGE",       STREAM_RANGE_VOLTS)
                _ljm.eWriteName(self._handle, f"{ch}_NEGATIVE_CH", 199)  # GND single-ended
            _ljm.eWriteName(self._handle, "STREAM_RESOLUTION_INDEX", res_index)

            # --- Start stream -------------------------------------------------
            # eStreamStart returns the actual scan rate the device settled on
            # (hardware rounds to the nearest achievable value).
            actual_rate = _ljm.eStreamStart(
                self._handle, scans_per_read, n_ch, scan_addresses, scan_rate
            )
            self._stream_active = True

            # --- Read loop ----------------------------------------------------
            while self._running:
                # eStreamRead blocks until scans_per_read scans are ready.
                # Returns: (flat_data_list, device_scan_backlog, ljm_scan_backlog)
                ret         = _ljm.eStreamRead(self._handle)
                flat        = np.asarray(ret[0], dtype=float)
                dev_backlog = int(ret[1])

                # Warn if the device buffer is accumulating (consumer too slow).
                if dev_backlog > scans_per_read * 2:
                    self.error.emit(
                        f"[{self._profile_name}] Device stream backlog "
                        f"{dev_backlog} scans (threshold {scans_per_read * 2}) "
                        "— drain thread may be too slow."
                    )

                # --- De-interleave -------------------------------------------
                # LJM flat layout (stride = n_ch, which MUST match len(scan_names)):
                #   [ch0_scan0, ch1_scan0, ..., chN-1_scan0,
                #    ch0_scan1, ch1_scan1, ..., chN-1_scan1, ...]
                # After reshape: data[scan_index, channel_index]
                # This assert fires immediately if profile/channel count mismatch.
                assert len(flat) % n_ch == 0, (
                    f"eStreamRead returned {len(flat)} values — "
                    f"not divisible by n_ch={n_ch} "
                    f"(profile '{self._profile_name}').  "
                    "Scan-list / channel-count mismatch; check that no scan-list "
                    "change was attempted mid-stream."
                )
                data = flat.reshape(-1, n_ch)   # shape: (scans_per_read, n_ch)

                payload = self._build_payload(
                    scan_names, data, scans_per_read, time.monotonic() - t0
                )
                self.window_ready.emit(payload)

        except Exception as exc:
            if self._running:   # suppress error noise on intentional stop
                self.error.emit(f"Stream error [{self._profile_name}]: {exc}")
        finally:
            # Always stop the stream before this thread exits.
            # Guard against double-stop (e.g. if eStreamStart itself failed).
            if self._stream_active:
                try:
                    _ljm.eStreamStop(self._handle)
                except Exception:
                    pass
                self._stream_active = False

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_payload(self, scan_names: list, data: np.ndarray,
                       scans_per_read: int, t: float) -> dict:
        """Build the window_ready payload from a de-interleaved data block.

        scan_names[i] corresponds to data[:, i] (ascending AIN order).
        Any amp or log-amp channel absent from scan_names receives a None
        entry (relevant for single-channel profiles, where only one amp
        channel is streamed and the other seven — plus all log amps — are
        paused).
        """
        channels: dict = {}

        for i, ain in enumerate(scan_names):
            col = data[:, i]   # one channel, all scans in this window
            if ain in AMP_CHANNELS:
                channels[ain] = {
                    "waveform": col.copy(),
                    "peak":   float(np.max(np.abs(col))),
                    "pk_pk":  float(col.max() - col.min()),
                    "rms":    float(np.sqrt(np.mean(col ** 2))),
                }
            else:
                # Log-amp channel: mean voltage over the window.
                # (Full waveform not needed; consumers convert mean -> current.)
                channels[ain] = {"mean": float(col.mean())}

        # Any channel absent from this profile's scan list -> None.
        # Consumers must display a "paused" state rather than stale numbers.
        for ain in AMP_CHANNELS + LOGAMP_CHANNELS:
            if ain not in scan_names:
                channels[ain] = None

        return {
            "profile":        self._profile_name,
            "window_samples": scans_per_read,
            "t":              t,
            "channels":       channels,
        }


# ---------------------------------------------------------------------------
# Self-test — no hardware required; tests de-interleave and windowing math
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    import os
    # Allow running as: python rbl/hardware/labjack_stream_worker.py from project root
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

    import numpy as np  # already imported above but explicit for clarity

    w = LabJackStreamWorker.__new__(LabJackStreamWorker)

    # --- WAVEFORM profile ---
    print("Testing WAVEFORM profile de-interleave...")
    w._profile_name = "WAVEFORM"
    win_w  = window_samples("WAVEFORM")
    sl_w   = list(STREAM_PROFILES["WAVEFORM"]["scan_list"])
    n_w    = len(sl_w)
    data_w = np.full((win_w, n_w), 2.0)
    p_w    = w._build_payload(sl_w, data_w, win_w, 0.1)

    assert p_w["profile"]        == "WAVEFORM"
    assert p_w["window_samples"] == win_w
    for ain in AMP_CHANNELS:
        assert p_w["channels"][ain] is not None,               f"{ain} missing"
        assert len(p_w["channels"][ain]["waveform"]) == win_w, f"{ain} wrong length"
        assert abs(p_w["channels"][ain]["peak"]  - 2.0) < 1e-9
        assert abs(p_w["channels"][ain]["pk_pk"] - 0.0) < 1e-9
        assert abs(p_w["channels"][ain]["rms"]   - 2.0) < 1e-9
    for ain in LOGAMP_CHANNELS:
        assert p_w["channels"][ain] is None, f"{ain} should be None in WAVEFORM"
    print(f"  [OK] WAVEFORM: stride={n_w}, window={win_w}, "
          f"amp peak=2.0 V, log amps=None")

    # --- FULL profile ---
    print("Testing FULL profile de-interleave...")
    w._profile_name = "FULL"
    win_f  = window_samples("FULL")
    sl_f   = list(STREAM_PROFILES["FULL"]["scan_list"])
    n_f    = len(sl_f)
    data_f = np.full((win_f, n_f), 1.5)
    for i, ain in enumerate(sl_f):
        if ain in LOGAMP_CHANNELS:
            data_f[:, i] = 4.5
    p_f = w._build_payload(sl_f, data_f, win_f, 0.2)

    assert p_f["profile"]        == "FULL"
    assert p_f["window_samples"] == win_f
    for ain in AMP_CHANNELS:
        assert p_f["channels"][ain] is not None
        assert len(p_f["channels"][ain]["waveform"]) == win_f
    for ain in LOGAMP_CHANNELS:
        assert p_f["channels"][ain] is not None
        assert "mean" in p_f["channels"][ain]
        assert abs(p_f["channels"][ain]["mean"] - 4.5) < 1e-9, \
            f"{ain} mean={p_f['channels'][ain]['mean']} != 4.5"
    print(f"  [OK] FULL: stride={n_f}, window={win_f}, logamp mean=4.5 V")

    # --- SINGLE_FAST profile (one amp channel; all others paused) ---
    print("Testing SINGLE_FAST single-channel de-interleave...")
    w._profile_name = "SINGLE_FAST"
    win_s   = window_samples("SINGLE_FAST")
    target  = "AIN9"                       # Y+ voltage monitor
    data_s  = np.full((win_s, 1), 3.3)
    p_s     = w._build_payload([target], data_s, win_s, 0.3)

    assert p_s["profile"]        == "SINGLE_FAST"
    assert p_s["window_samples"] == win_s
    assert p_s["channels"][target] is not None
    assert len(p_s["channels"][target]["waveform"]) == win_s
    assert abs(p_s["channels"][target]["peak"] - 3.3) < 1e-9
    # Every other amp channel and every log amp must be paused (None).
    for ain in AMP_CHANNELS:
        if ain != target:
            assert p_s["channels"][ain] is None, f"{ain} should be paused"
    for ain in LOGAMP_CHANNELS:
        assert p_s["channels"][ain] is None, f"{ain} should be paused"
    print(f"  [OK] SINGLE_FAST: target={target}, window={win_s}, "
          f"7 amps + 4 log amps paused")

    print("[OK] labjack_stream_worker self-test passed")
