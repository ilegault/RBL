"""
funcgen_control.py
Beamline's function-generator half: the two DG1022Z units, the readback that
becomes a FuncGenState, and the command surface every screen has to go
through to reach them.

Split out of beamline.py — see labjack_link.py's module docstring for why
these are mixins rather than separate objects.

THE COMMAND SURFACE IS THE POINT
--------------------------------
The ±5 V combined-peak interlock and the both-EXT timebase guard live in this
file and nowhere else. Both protect hardware — the first the amplifier input,
the second the two instruments' rear-panel 10 MHz connectors — and both are
enforced on the single path to the driver rather than in whichever widget
offers the control. A control that reached a generator by any other route
would bypass them entirely, which is exactly the arrangement this replaced:
the checks used to sit behind one tab's Apply button, and a second screen
offering the same control had no way to inherit them.

Failures are reported via `command_failed` rather than raised, so a bad
command from a screen cannot take down the Qt event loop.
"""
import time

from rbl.hardware.funcgen_safety import channel_peak_volts, PEAK_MAX_VOLTS
from rbl.state.setpoints import FuncGenSetpoints
from rbl.state.snapshots import ChannelSnapshot, ChannelParams, FuncGenState


class FuncGenControlMixin:
    """DG1022Z ownership, readback ingestion, and the guarded command surface.

    Mixed into Beamline. Requires the host class to declare: funcgens_changed,
    timebase_changed, command_failed.
    """

    SETTLE_S = 3.0   # PLL settling time before the lock readback is trusted

    def _init_funcgens(self):
        # None until FuncGenTab connects them (each needs a VISA resource
        # string at construction time, unlike Galil/LabJack).
        self.dg_a = None
        self.dg_b = None

        # The four channels' commanded parameters, shared by every screen that
        # edits them (Function Generators per channel, Overview per axis). One
        # model, so the two can never show different setpoints for the same
        # channel and an Apply from either cannot silently overwrite the other.
        self.funcgen_setpoints = FuncGenSetpoints(self)

        # Last-read clock source per unit, refreshed by read_timebase() and
        # republished on every FuncGenState — see that method's docstring.
        self._timebase = {"A": "—", "B": "—"}

    # ---- Readback -------------------------------------------------------------

    def ingest_funcgen_readback(self, connected: dict, timebase: dict, readback: dict):
        """connected: {"A": bool, "B": bool}. timebase: {"A": "INT"/"EXT", ...}.
        readback: {"A1": {shape, freq, amp, offset, phase, output}, ...} —
        exactly DG1022Z.get_state()'s shape, keyed by channel key.
        """
        channels = {}
        for key, state in readback.items():
            if "error" in state:
                continue
            channels[key] = ChannelSnapshot(
                shape=state["shape"],
                freq_hz=state["freq"],
                amp_vpp=state["amp"],
                offset_v=state["offset"],
                phase_deg=state["phase"],
                output_on=state["output"],
            )
        # An empty `timebase` means "no fresh reading" — carry the cached one
        # rather than publishing blanks that would flicker a second screen's
        # lock indicator off and on between clock reads.
        self.funcgens_changed.emit(FuncGenState(
            connected=dict(connected),
            timebase=dict(timebase) if timebase else dict(self._timebase),
            channels=channels,
        ))

    def funcgens_disconnected(self):
        self._timebase = {"A": "—", "B": "—"}
        self.funcgens_changed.emit(FuncGenState(
            connected={"A": False, "B": False}, timebase=dict(self._timebase),
            channels={},
        ))

    # ---- Command surface ------------------------------------------------------

    def _gen_for(self, gen_letter: str):
        return self.dg_a if gen_letter == "A" else self.dg_b

    def set_channel(self, key: str, params: ChannelParams) -> bool:
        """Push one channel's parameters to its generator.

        `key` is e.g. "A1" (generator letter + channel number). Returns True
        on success. The combined-peak interlock (|offset| + amp/2) is
        enforced unconditionally: a peak above PEAK_MAX_VOLTS is rejected
        here regardless of what any caller already checked.
        """
        gen_letter, channel = key[0], int(key[1])
        gen = self._gen_for(gen_letter)
        if gen is None:
            self.command_failed.emit("funcgen", f"{key}: generator not connected")
            return False

        peak = channel_peak_volts(params.shape, params.amp_vpp, params.offset_v)
        if peak > PEAK_MAX_VOLTS + 1e-9:
            self.command_failed.emit(
                "funcgen",
                f"{key}: combined peak {peak:.4g} V exceeds the "
                f"{PEAK_MAX_VOLTS:.0f} V amplifier input limit",
            )
            return False

        try:
            warn = gen.set_waveform(channel, params.shape, params.freq_hz,
                                     params.amp_vpp, params.offset_v, params.phase_deg)
            gen.set_output_load(channel, params.load)
            gen.set_start_phase(channel, params.start_phase_deg)
            if params.output_on:
                gen.output_on(channel)
            else:
                gen.output_off(channel)
            if warn:
                self.command_failed.emit("funcgen", f"{key}: {warn}")
            return True
        except Exception as e:
            self.command_failed.emit("funcgen", f"{key}: {e}")
            return False

    def apply_all_channels(self, params_by_key: dict) -> bool:
        """Configure + enable every given channel together.

        `params_by_key`: {"A1": ChannelParams, ...} for whichever channels
        the caller wants applied — channels whose generator isn't connected
        are silently skipped.

        Preserves the three-phase ordering exactly (load-bearing for raster
        alignment): configure every channel first (outputs untouched), THEN
        fire every output-enable back-to-back, THEN run :PHASe:SYNChronize
        last on each connected unit, once the relays have settled.
        :OUTPut ON only closes a relay — it does not reset the waveform's DDS
        phase accumulator, so aligning before the relays are closed would
        lock in the wrong start point.

        The interlock is checked for EVERY channel before anything is sent —
        applying a partial raster is worse than applying none, so if any
        channel is over the hard ceiling, nothing goes out at all.
        """
        active = []   # (key, gen_letter, channel, gen, params)
        for key, params in params_by_key.items():
            gen = self._gen_for(key[0])
            if gen is not None:
                active.append((key, key[0], int(key[1]), gen, params))
        if not active:
            return False

        blocked = []
        for key, _, _, _, params in active:
            peak = channel_peak_volts(params.shape, params.amp_vpp, params.offset_v)
            if peak > PEAK_MAX_VOLTS + 1e-9:
                blocked.append(key)
        if blocked:
            self.command_failed.emit(
                "funcgen",
                "Apply All blocked — over the "
                f"{PEAK_MAX_VOLTS:.0f} V limit: " + ", ".join(blocked),
            )
            return False

        # Phase 1: configure every channel (outputs untouched).
        try:
            for key, gen_letter, channel, gen, params in active:
                warn = gen.set_waveform(channel, params.shape, params.freq_hz,
                                         params.amp_vpp, params.offset_v, params.phase_deg)
                gen.set_output_load(channel, params.load)
                gen.set_start_phase(channel, params.start_phase_deg)
                if warn:
                    self.command_failed.emit("funcgen", f"{key}: {warn}")
        except Exception as e:
            self.command_failed.emit("funcgen", f"Apply All failed during configure: {e}")
            return False

        # Phase 2: enable outputs — OFF ones first, then all ON back-to-back.
        try:
            for key, gen_letter, channel, gen, params in active:
                if not params.output_on:
                    gen.output_off(channel)
            for key, gen_letter, channel, gen, params in active:
                if params.output_on:
                    gen.output_on(channel)
        except Exception as e:
            self.command_failed.emit("funcgen", f"Apply All failed during output enable: {e}")
            return False

        # Phase 3: align each connected unit's two channels, last.
        time.sleep(0.05)   # let the output relays physically settle first
        for gen_letter in ("A", "B"):
            gen = self._gen_for(gen_letter)
            if gen is not None:
                try:
                    gen.align_phase(1)
                except Exception as e:
                    self.command_failed.emit("funcgen", f"Gen {gen_letter}: align phase failed: {e}")

        return True

    # ---- Cross-unit timebase (10 MHz reference) --------------------------------

    def read_timebase(self) -> dict:
        """Each unit's active clock source: "INT", "EXT", "?" or "—".

        Queries the instruments and caches the answer. The cache is what gets
        published on every FuncGenState, so a second screen can show the lock
        state without either polling the clock source at readback rate (two
        extra VISA round trips every 500 ms for a value that only changes when
        somebody changes it) or reaching for a driver of its own.
        """
        clocks = {}
        for letter in ("A", "B"):
            gen = self._gen_for(letter)
            if gen is None:
                clocks[letter] = "—"
                continue
            try:
                clocks[letter] = gen.get_reference_clock()
            except Exception:
                clocks[letter] = "?"
        self._timebase = clocks
        return dict(clocks)

    @property
    def timebase_locked(self) -> bool:
        """True only for the one configuration that actually shares a clock."""
        return self._timebase.get("A") == "INT" and self._timebase.get("B") == "EXT"

    def set_shared_timebase(self, enabled: bool):
        """Lock (or unlock) Gen B to Gen A's 10 MHz reference.

        Returns (ok, message): ok is True when the REQUESTED state was
        actually reached — a failed lock returns False so the caller can put
        its checkbox back rather than showing a lock that isn't there.

        Only Gen B is ever set to EXT. The rear-panel [10MHz In/Out] connector
        is BIDIRECTIONAL and its direction follows the clock-source setting,
        so two units both driving it is not a misconfiguration to warn about
        afterwards — it damages the instruments. Hence the guard below runs
        before anything is written.
        """
        gen_a, gen_b = self._gen_for("A"), self._gen_for("B")
        if gen_a is None or gen_b is None:
            return False, "Both generators must be connected to share a timebase."

        try:
            if not enabled:
                gen_a.set_reference_clock("INTernal")
                gen_b.set_reference_clock("INTernal")
                self.timebase_changed.emit(self.read_timebase())  # refreshes cache
                return True, ("Independent internal clocks — X/Y phase will "
                              "drift across the two units.")

            if gen_a.get_reference_clock() == "EXT":
                msg = (
                    "Gen A is currently set to EXTernal reference.\n\n"
                    "One unit must drive the 10 MHz reference (INT) and the "
                    "other must follow it (EXT). Setting both to EXT causes "
                    "both instruments to drive the rear-panel [10MHz In/Out] "
                    "connector simultaneously — this will damage the "
                    "instruments.\n\n"
                    "Return Gen A to its internal clock first (send "
                    ":SYSTem:ROSCillator:SOURce INTernal to Gen A via the SCPI "
                    "console), then enable sharing."
                )
                self.command_failed.emit(
                    "funcgen",
                    "Timebase refused — Gen A is already EXT; both EXT would collide")
                return False, msg

            gen_a.set_reference_clock("INTernal")
            locked, actual = gen_b.verify_external_lock(settle_s=self.SETTLE_S)
            self.timebase_changed.emit(self.read_timebase())
            if locked:
                return True, "Locked: Gen B follows Gen A's 10 MHz reference."
            return False, (
                f"Gen B was set to external 10 MHz reference but its readback "
                f"is {actual!r} — the DG1022Z silently falls back to INT when "
                f"no valid signal is present.\n\n"
                "Checklist:\n"
                "  • BNC cable from Gen A [10MHz Out] → Gen B [10MHz In]\n"
                "  • Reference level must be 250 mVpp – 5 Vpp\n"
                "  • The [10MHz In/Out] connector is BIDIRECTIONAL — its "
                "direction is set by the clock source selection. Both units "
                "set to INT will each try to drive the connector "
                "simultaneously, which can damage the instruments."
            )
        except Exception as e:
            self.command_failed.emit("funcgen", f"timebase: {e}")
            return False, str(e)

    def all_outputs_off(self):
        """Turn off every function-generator channel's output.

        Deliberately narrow: this only opens the output relays, the same as
        a manual "Output OFF" click on each channel. It does not disconnect,
        zero any setpoint, or otherwise touch the generators.
        """
        for gen_letter in ("A", "B"):
            gen = self._gen_for(gen_letter)
            if gen is None:
                continue
            for channel in (1, 2):
                try:
                    gen.output_off(channel)
                except Exception as e:
                    self.command_failed.emit("funcgen", f"{gen_letter}{channel}: {e}")
