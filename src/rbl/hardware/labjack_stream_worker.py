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
      "window_samples": int   — samples per channel actually in this window.
                               Normally window_samples(profile), but the FIRST
                               window after every eStreamStart is shorter by
                               STREAM_SETTLE_DISCARD_S worth of scans, which
                               are trimmed as mux/PGA settling. Consumers must
                               read this (or len(waveform)) rather than assume
                               the profile's nominal size.
      "t":              float — sample-accurate elapsed seconds at this
                               window's LAST sample (cumulative sample count
                               / actual scan rate; not a wall-clock read)
      "sample_period":  float — seconds per sample (1 / actual scan rate)
      "channels": {
          "AIN6":  {"waveform": np.ndarray,   # raw volts
                    "peak":     float,         # signed sample with max |amplitude|
                    "pk_pk":    float,         # max - min in volts
                    "rms":      float,         # RMS in volts
                    "mean":     float,         # arithmetic mean in volts (DC estimator)
                    "std":      float},        # standard deviation in volts (noise estimator)
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
    STREAM_PROFILES, STREAM_RANGE_VOLTS, STREAM_SETTLE_DISCARD_S,
    AMP_CHANNELS, LOGAMP_CHANNELS, DEFAULT_PROFILE, window_samples,
    resolution_index, is_single_channel, channel_choices,
    is_pair_channel, pair_choices, pair_scan_list, DEFAULT_AMP_PAIR,
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
                 channel_override: str = None, t0: float = None, parent=None):
        """
        Parameters
        ----------
        handle           : int
            Open LJM handle from LabJackT7.handle.  Must remain valid for the
            lifetime of this worker.  Do NOT close it here.
        profile_name     : str
            One of the keys in STREAM_PROFILES.
        channel_override : str, optional
            What this means depends on the profile:
              * single-channel ("SINGLE_FAST" / "SINGLE_HIRES") — the AIN
                name to stream (e.g. "AIN7"), from the profile's
                channel_choices.  Defaults to the profile's scan_list[0].
              * pair ("AMP_PAIR") — the AMP LABEL whose (current, voltage)
                pair to stream (e.g. "Y+"), from the profile's pair_choices.
                Defaults to DEFAULT_AMP_PAIR.  It is an amp label and not an
                AIN because the two AINs of a pair always travel together.
              * multi-channel ("WAVEFORM" / "FULL") — ignored; the scan list
                is fixed.
        t0               : float, optional
            Shared ``time.monotonic()`` epoch for the emitted ``t`` timestamps.
            Switching profiles/channels destroys this worker and builds a new
            one; passing the SAME epoch across restarts keeps the payload ``t``
            monotonic so downstream history buffers do not jump backwards (which
            otherwise makes the plot appear to reset on every mode change).
            If None, the worker starts its own epoch at run().
        """
        super().__init__(parent)
        self._handle           = handle
        self._profile_name     = profile_name
        self._channel_override = channel_override
        self._t0               = t0
        self._running          = True
        self._stream_active    = False

    def stop(self):
        """Signal the read loop to exit.  Call wait(ms) afterwards."""
        self._running = False

    def _resolve_scan_names(self):
        """(scan_names, error_message) for the active profile and override.

        Split out of run() so the mapping from (profile, override) to a scan
        list can be tested without a T7 attached — it is the part most likely
        to file data against the wrong amplifier if it is ever wrong, and
        that is a failure that produces plausible-looking numbers rather than
        an exception.

        Returns ([], "reason") on a bad target.  Never falls back to a
        default on an invalid override: streaming the wrong channel silently
        is worse than refusing to stream.
        """
        profile = STREAM_PROFILES[self._profile_name]

        # Ascending physical AIN order throughout — see
        # rbl/config/labjack_stream_config.py's module docstring.
        if is_single_channel(self._profile_name):
            choices = channel_choices(self._profile_name)
            ch      = self._channel_override or profile["scan_list"][0]
            if ch not in choices:
                return [], (f"[{self._profile_name}] channel '{ch}' is not a "
                            f"valid single-channel target (choices: {choices}).")
            return [ch], ""

        if is_pair_channel(self._profile_name):
            # A pair profile is targeted by AMP LABEL, not AIN name, so the
            # override carries "Y+" rather than "AIN8".  The two AINs of a
            # pair always travel together, so exposing them independently
            # would only create ways to select an incoherent scan list.
            choices = pair_choices(self._profile_name)
            amp     = self._channel_override or DEFAULT_AMP_PAIR
            if amp not in choices:
                return [], (f"[{self._profile_name}] amp '{amp}' is not a "
                            f"valid pair target (choices: {choices}).")
            return pair_scan_list(amp), ""

        return list(profile["scan_list"]), ""

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
        scan_names, err = self._resolve_scan_names()
        if err:
            self.error.emit(err)
            return
        scan_rate      = profile["per_channel_rate_hz"]
        res_index      = resolution_index(self._profile_name)
        n_ch           = len(scan_names)
        scans_per_read = window_samples(self._profile_name)

        # AIN name -> Modbus address.  Computed once; stride = n_ch in the
        # de-interleave reshape below.  If this list and n_ch ever disagree,
        # the assert inside the read loop will catch it immediately.
        scan_addresses = [_ain_address(ch) for ch in scan_names]

        # Use the shared epoch when the owner supplied one so timestamps stay
        # continuous across a stop/reconfigure/start cycle; otherwise anchor here.
        t0 = self._t0 if self._t0 is not None else time.monotonic()

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

            # --- Sample-accurate timeline -------------------------------------
            # Each window's timestamp is derived from a CUMULATIVE SAMPLE COUNT
            # (scans_total / actual_rate), NOT from a wall-clock read taken when
            # the window finishes.  A wall-clock timestamp jitters by the thread-
            # scheduling / eStreamRead latency of each read, and that jitter was
            # displacing every 0.1 s waveform chunk horizontally — so when the
            # amp tab stitched ~10 chunks together for a 62.5 ms–1 s snapshot the
            # trace broke ("tripped") at each seam.  A count-based clock advances
            # in exact sample steps, so consecutive chunks butt together
            # seamlessly.  sample_period travels in the payload so consumers can
            # reconstruct each sample's true time.  t_base seeds elapsed time
            # from the shared epoch, keeping timestamps monotonic across a
            # stop/reconfigure/start cycle.
            sample_period = 1.0 / actual_rate if actual_rate else 1.0 / scan_rate
            t_base        = time.monotonic() - t0
            scans_total   = 0

            # --- Post-start settling trim -------------------------------------
            # The scan list was just reconfigured, so the mux and PGA are still
            # settling when the first scans land. Those samples are not a
            # measurement of anything, and downstream they are indistinguishable
            # from a real signal — the calibration over-current interlock was
            # reading them as a current spike and aborting runs at 0 V
            # commanded.
            #
            # The discard is expressed in TIME and consumed across however many
            # windows it spans. At 100 ms it is exactly one whole 100 ms window,
            # which is then never emitted at all; at 20 ms it is a leading slice
            # of the first window and the remainder is delivered normally. This
            # has to handle both, because the setting was raised from 1 ms to
            # 20 ms to 100 ms chasing an artifact that outlasted each estimate,
            # and a trim that silently capped itself at one-window-minus-a-
            # sample would have delivered a 1-sample payload instead.
            settle_remaining = int(round(
                STREAM_SETTLE_DISCARD_S * (actual_rate or scan_rate)))
            settle_total = settle_remaining
            settle_peak  = 0.0   # largest |sample| seen anywhere in the discard

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

                # Advance the sample-accurate clock by the scans just read, then
                # stamp this window with the time of its LAST sample.  The clock
                # counts every scan the device produced, INCLUDING any settling
                # scans trimmed below — they occupied real time on the wire, and
                # not counting them would shift the whole timeline earlier and
                # reintroduce the seam this clock exists to avoid.
                scans_total += data.shape[0]
                t = t_base + scans_total * sample_period

                if settle_remaining > 0:
                    take    = min(settle_remaining, data.shape[0])
                    dropped = data[:take]
                    data    = data[take:]
                    settle_remaining -= take
                    try:
                        settle_peak = max(settle_peak,
                                          float(np.max(np.abs(dropped))))
                        edge_pk = float(np.max(np.abs(
                            dropped[-max(take // 10, 1):])))
                    except Exception:
                        edge_pk = float("nan")   # diagnostics never break the loop

                    if settle_remaining > 0:
                        # The trim spans more than this window: emit nothing.
                        # Consumers see a gap, which is correct — there is no
                        # measurement here to give them. The clock above has
                        # already counted these scans, so the next window's
                        # timestamp still lands where it belongs.
                        continue

                    # Trim complete. Report how big the artifact was and,
                    # critically, whether it had decayed by the trim's edge. If
                    # the edge peak is still far above the kept peak the
                    # settling outlasted the trim, its tail is in the data
                    # being delivered, and the number to raise is
                    # STREAM_SETTLE_DISCARD_S. If the edge peak has come down
                    # and the interlock still fires, the current is real and
                    # the ladder is what should change.
                    kept_pk = (float(np.max(np.abs(data)))
                               if data.size else float("nan"))
                    print(f"[STREAM] {self._profile_name}: discarded "
                          f"{settle_total} settling scans "
                          f"({settle_total * sample_period * 1e3:.1f} ms) "
                          f"after stream start — peak in discard "
                          f"{settle_peak:.3f} V, at trim edge {edge_pk:.3f} V, "
                          f"kept-window peak {kept_pk:.3f} V")
                    if data.shape[0] == 0:
                        continue   # trim ended exactly on the window boundary

                payload = self._build_payload(
                    scan_names, data, data.shape[0], t, sample_period
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
                       scans_per_read: int, t: float,
                       sample_period: float = None) -> dict:
        """Build the window_ready payload from a de-interleaved data block.

        scan_names[i] corresponds to data[:, i] (ascending AIN order).
        Any amp or log-amp channel absent from scan_names receives a None
        entry (relevant for single-channel profiles, where only one amp
        channel is streamed and the other seven — plus all log amps — are
        paused).

        ``sample_period`` is the per-sample time step (1 / actual scan rate).
        It is carried through so waveform consumers can reconstruct exact
        sample times and stitch consecutive windows without seams.  It is
        optional so pure payload-math callers (self-test, unit tests) need not
        supply it.
        """
        channels: dict = {}

        for i, ain in enumerate(scan_names):
            col = data[:, i]   # one channel, all scans in this window
            if ain in AMP_CHANNELS:
                channels[ain] = {
                    "waveform": col.copy(),
                    "peak":   float(col[np.argmax(np.abs(col))]),
                    "pk_pk":  float(col.max() - col.min()),
                    "rms":    float(np.sqrt(np.mean(col ** 2))),
                    "mean":   float(np.mean(col)),
                    "std":    float(np.std(col)),
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
            "sample_period":  sample_period,
            "channels":       channels,
        }


# ---------------------------------------------------------------------------
# Self-test — no hardware required; tests de-interleave and windowing math
# ---------------------------------------------------------------------------

if __name__ == "__main__":
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
        assert abs(p_w["channels"][ain]["mean"]  - 2.0) < 1e-9
        assert abs(p_w["channels"][ain]["std"]   - 0.0) < 1e-9
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
