"""
amp_drive.py
Clamped, error-checked, terminal-logged drive commands for the EEL5000 amplifier.

SCOPE
-----
Non-Qt class. Every method is synchronous and short (one SCPI write). Safe to
call from a Qt slot. Nothing here sleeps or iterates over setpoints.

Instantiated by CalibrationRunner (prefix "[CAL]"). It was extracted when a
second runner (the amp test matrix, since removed) needed the identical
zero-and-off shutdown path; keeping it separate is still worth it, because the
shutdown sequence is safety-critical and should have exactly one definition
whatever ends up calling it.
"""
import atexit
import logging

from rbl.config.hardware_config import AMP_MAX_KV
from rbl.hardware.funcgen_driver import MAX_AMP_VPP
from rbl.hardware.funcgen_safety import _AMP_GAIN

log = logging.getLogger(__name__)


class AmpDrive:
    """Owns no hardware. Wraps the {amp_label: (DG1022Z, channel)} map with
    clamped, error-checked, terminal-logged commands and one shutdown path.

    Args:
        funcgen_map: dict mapping amp label (e.g. "X+") to (DG1022Z, channel_int).
        max_kv:      hard ceiling on |commanded kV|. Defaults to AMP_MAX_KV.
        log_prefix:  prefix for terminal log lines, e.g. "[CAL]" or "[AMT]".
    """

    def __init__(self, funcgen_map: dict, max_kv: float = AMP_MAX_KV,
                 log_prefix: str = "[AMT]"):
        self._map    = funcgen_map
        self._max_kv = max_kv
        self._pfx    = log_prefix
        self._ramp_engine = None   # attached lazily; see attach_ramp_engine()

    # ------------------------------------------------------------------
    # State snapshot
    # ------------------------------------------------------------------

    def snapshot_all(self) -> dict:
        """Return a {label: get_state(channel)} dict for all amps.

        Failed reads produce None for that label (with a printed warning).
        """
        out = {}
        for label, (gen, channel) in self._map.items():
            try:
                out[label] = gen.get_state(channel)
            except Exception as e:
                print(f"{self._pfx} WARN snapshot {label} ch{channel}: {e}")
                log.warning("snapshot %s ch%s: %s", label, channel, e)
                out[label] = None
        return out

    # ------------------------------------------------------------------
    # Drive commands
    # ------------------------------------------------------------------

    def command_dc(self, label: str, kv: float) -> None:
        """Command a DC setpoint (kV), clamped to ±max_kv."""
        clamped = max(-self._max_kv, min(self._max_kv, kv))
        gen_v   = clamped * 1000.0 / _AMP_GAIN
        gen, channel = self._map[label]
        print(f"{self._pfx} {label} ch{channel}: DC {gen_v:+.4f} V "
              f"({clamped:+.4f} kV)")
        try:
            warn = gen.set_waveform(channel, "DC", 0.0, 0.0, gen_v, 0.0)
            if warn:
                print(f"{self._pfx} WARN {label} ch{channel}: {warn}")
                log.warning("%s ch%s: %s", label, channel, warn)
            gen.output_on(channel)
        except Exception as e:
            print(f"{self._pfx} ERROR {label} ch{channel}: {e}")
            log.error("%s ch%s: %s", label, channel, e)
            raise

    def command_sine(self, label: str, peak_kv: float, freq_hz: float,
                     phase_deg: float = 0.0) -> None:
        """Command a symmetric sine wave; peak_kv clamped to [0, max_kv]."""
        self._command_ac(label, peak_kv, freq_hz, "Sine", "SIN", phase_deg)

    def command_triangle(self, label: str, peak_kv: float, freq_hz: float,
                         phase_deg: float = 0.0) -> None:
        """Command a 50%-symmetry triangle wave; peak_kv clamped to [0, max_kv]."""
        self._command_ac(label, peak_kv, freq_hz, "Triangle", "TRI", phase_deg)

    def command_square(self, label: str, peak_kv: float, freq_hz: float,
                       phase_deg: float = 0.0) -> None:
        """Command a symmetric square wave; peak_kv clamped to [0, max_kv]."""
        self._command_ac(label, peak_kv, freq_hz, "Square", "SQU", phase_deg)

    def _command_ac(self, label: str, peak_kv: float, freq_hz: float,
                    shape: str, abbrev: str, phase_deg: float = 0.0) -> None:
        clamped = max(0.0, min(self._max_kv, peak_kv))
        gen_vpp = clamped * 2.0 * 1000.0 / _AMP_GAIN
        if gen_vpp > MAX_AMP_VPP:
            print(f"{self._pfx} WARN {label}: gen_vpp {gen_vpp:.4f} > "
                  f"MAX_AMP_VPP {MAX_AMP_VPP} — clamping")
            log.warning("%s: gen_vpp %.4f > MAX_AMP_VPP %.4f — clamping",
                        label, gen_vpp, MAX_AMP_VPP)
            gen_vpp = MAX_AMP_VPP
        gen, channel = self._map[label]
        print(f"{self._pfx} {label} ch{channel}: {abbrev} {freq_hz:.1f} Hz "
              f"{gen_vpp:.4f} Vpp ({clamped:.4f} kV peak) phase={phase_deg:.1f}°")
        try:
            warn = gen.set_waveform(channel, shape, freq_hz, gen_vpp, 0.0, phase_deg)
            if warn:
                print(f"{self._pfx} WARN {label} ch{channel}: {warn}")
                log.warning("%s ch%s: %s", label, channel, warn)
            gen.output_on(channel)
        except Exception as e:
            print(f"{self._pfx} ERROR {label} ch{channel}: {e}")
            log.error("%s ch%s: %s", label, channel, e)
            raise

    # ------------------------------------------------------------------
    # Ramped drive commands (Section 5.4) — the GUI's path to a target.
    # These delegate to a RampEngine; command_dc/command_sine/etc. above stay
    # unramped and are what the calibration/characterization services keep
    # using, since they deliberately step.
    # ------------------------------------------------------------------

    def attach_ramp_engine(self, ramp_engine) -> None:
        """Attach the RampEngine that command_*_ramped() delegate to."""
        self._ramp_engine = ramp_engine

    def command_dc_ramped(self, label: str, kv: float) -> None:
        """Ramp toward a DC setpoint (kV) instead of stepping immediately.

        Converts to generator volts the same way command_dc() does, then
        hands the target to RampEngine in ITS units (raw generator volts —
        see ramp_engine.py's module docstring for why the conversion lives
        here and not in the engine).
        """
        if self._ramp_engine is None:
            raise RuntimeError("no RampEngine attached; call attach_ramp_engine() first")
        clamped = max(-self._max_kv, min(self._max_kv, kv))
        gen_v = clamped * 1000.0 / _AMP_GAIN
        self._ramp_engine.retarget(label, gen_v, mode="offset")

    def command_ac_amplitude_ramped(self, label: str, peak_kv: float) -> None:
        """Ramp an AC drive's amplitude toward `peak_kv`.

        Frequency, shape, and phase are NOT ramped — they must already be
        configured on the channel (via command_sine/triangle/square) before
        this is called; only the amplitude changes, exactly like the front
        panel's amplitude knob.
        """
        if self._ramp_engine is None:
            raise RuntimeError("no RampEngine attached; call attach_ramp_engine() first")
        clamped = max(0.0, min(self._max_kv, peak_kv))
        gen_vpp = clamped * 2.0 * 1000.0 / _AMP_GAIN
        self._ramp_engine.retarget(label, gen_vpp, mode="amplitude")

    # ------------------------------------------------------------------
    # Shutdown primitives
    # ------------------------------------------------------------------

    def zero_all(self) -> None:
        """Command DC 0 V on all channels; outputs left in their current state."""
        for label, (gen, channel) in self._map.items():
            try:
                gen.set_waveform(channel, "DC", 0.0, 0.0, 0.0, 0.0)
            except Exception as e:
                print(f"{self._pfx} ERROR zero {label} ch{channel}: {e}")
                log.error("zero %s ch%s: %s", label, channel, e)
                raise

    def output_off(self, label: str) -> None:
        """Disable output on a single channel."""
        gen, channel = self._map[label]
        try:
            gen.output_off(channel)
        except Exception as e:
            print(f"{self._pfx} ERROR output_off {label} ch{channel}: {e}")
            log.error("output_off %s ch%s: %s", label, channel, e)
            raise

    def outputs_off_all(self) -> None:
        """Disable output on all channels."""
        for label, (gen, channel) in self._map.items():
            try:
                gen.output_off(channel)
            except Exception as e:
                print(f"{self._pfx} ERROR output_off {label} ch{channel}: {e}")
                log.error("output_off %s ch%s: %s", label, channel, e)
                raise

    def zero_and_off_all(self) -> None:
        """Best-effort zero + output-off on every channel.

        Each channel is wrapped in its own try/except so a failure on one
        channel never prevents the remaining channels from being zeroed.
        """
        for label, (gen, channel) in self._map.items():
            try:
                gen.set_waveform(channel, "DC", 0.0, 0.0, 0.0, 0.0)
            except Exception as e:
                print(f"{self._pfx} ERROR zero {label} ch{channel}: {e}")
                log.error("zero %s ch%s: %s", label, channel, e)
            try:
                gen.output_off(channel)
            except Exception as e:
                print(f"{self._pfx} ERROR output_off {label} ch{channel}: {e}")
                log.error("output_off %s ch%s: %s", label, channel, e)

    def restore_all(self, snapshot: dict) -> None:
        """Restore waveform settings from a snapshot_all() result.

        Output is intentionally left OFF after restore — the pre-run state
        is reinstated but the amplifier is not re-armed.
        """
        for label, (gen, channel) in self._map.items():
            snap = snapshot.get(label)
            if not snap or "error" in snap:
                continue
            try:
                gen.set_waveform(channel, snap["shape"], snap["freq"],
                                 snap["amp"], snap["offset"], snap["phase"])
                gen.set_output_load(channel, snap.get("load", "INFinity"))
            except Exception as e:
                print(f"{self._pfx} ERROR restore {label} ch{channel}: {e}")
                log.error("restore %s ch%s: %s", label, channel, e)

    def register_atexit(self) -> None:
        """Register zero_and_off_all as an atexit handler (never raises)."""
        def _handler():
            try:
                self.zero_and_off_all()
            except Exception:
                pass
        atexit.register(_handler)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from rbl.config.hardware_config import AMP_LABELS, AMP_CHANNEL_MAP

    class _FakeGen:
        def __init__(self):
            self.calls = []
        def set_waveform(self, ch, shape, freq, amp, offset, phase):
            self.calls.append(("set_waveform", ch, shape, freq, amp, offset, phase))
            return ""
        def output_on(self, ch):
            self.calls.append(("output_on", ch))
        def output_off(self, ch):
            self.calls.append(("output_off", ch))
        def get_state(self, ch):
            return {"shape": "DC", "freq": 0.0, "amp": 0.0, "offset": 0.0,
                    "phase": 0.0, "output": "OFF", "load": "INFinity"}
        def set_output_load(self, ch, load):
            self.calls.append(("set_output_load", ch, load))

    fake_a = _FakeGen()
    fake_b = _FakeGen()
    fmap = {"X+": (fake_a, 1), "X-": (fake_a, 2),
            "Y+": (fake_b, 1), "Y-": (fake_b, 2)}
    drive = AmpDrive(fmap, max_kv=5.0, log_prefix="[TEST]")

    # command_dc clamp
    fake_a.calls.clear()
    drive.command_dc("X+", 9.0)
    sw = next(c for c in fake_a.calls if c[0] == "set_waveform")
    gen_v = sw[5]  # offset position = DC offset
    assert abs(gen_v - 5.0) < 1e-9, f"expected 5.0 V gen_v, got {gen_v}"
    print("[OK] command_dc(X+, 9.0) -> clamped to 5.0 kV -> 5.0 V gen")

    # command_sine exact Vpp at max
    fake_a.calls.clear()
    drive.command_sine("X+", 5.0, 1000.0)
    sw = next(c for c in fake_a.calls if c[0] == "set_waveform")
    assert sw[2] == "Sine", f"shape should be 'Sine', got {sw[2]!r}"
    vpp = sw[4]
    assert abs(vpp - MAX_AMP_VPP) < 1e-9, f"expected {MAX_AMP_VPP} Vpp, got {vpp}"
    print(f"[OK] command_sine(X+, 5.0, 1000) -> {vpp:.4f} Vpp (= MAX_AMP_VPP)")

    # command_sine clamp beyond max_kv
    fake_a.calls.clear()
    drive.command_sine("X+", 6.0, 1000.0)
    sw = next(c for c in fake_a.calls if c[0] == "set_waveform")
    vpp2 = sw[4]
    assert vpp2 <= MAX_AMP_VPP, f"Vpp {vpp2} exceeds MAX_AMP_VPP {MAX_AMP_VPP}"
    print(f"[OK] command_sine(X+, 6.0, 1000) -> clamped to {vpp2:.4f} Vpp")

    # zero_and_off_all survives one channel raising
    class _FaultGen(_FakeGen):
        def set_waveform(self, ch, shape, freq, amp, offset, phase):
            if ch == 1:
                raise RuntimeError("simulated fault")
            return super().set_waveform(ch, shape, freq, amp, offset, phase)
    fault_a = _FaultGen()
    fault_b = _FakeGen()
    fmap2 = {"X+": (fault_a, 1), "X-": (fault_a, 2),
             "Y+": (fault_b, 1), "Y-": (fault_b, 2)}
    drive2 = AmpDrive(fmap2, max_kv=5.0, log_prefix="[TEST]")
    drive2.zero_and_off_all()   # must not raise
    off_calls = [c for c in fault_b.calls if c[0] == "output_off"]
    assert len(off_calls) == 2, f"expected 2 output_off on fault_b, got {off_calls}"
    print("[OK] zero_and_off_all continues past a set_waveform fault")

    # restore_all never calls output_on
    snap = drive.snapshot_all()
    fake_a.calls.clear()
    fake_b.calls.clear()
    drive.restore_all(snap)
    for gen in (fake_a, fake_b):
        assert not any(c[0] == "output_on" for c in gen.calls), \
            f"restore_all must not call output_on; got {gen.calls}"
    print("[OK] restore_all never calls output_on")

    # command_square shape
    fake_a.calls.clear()
    drive.command_square("X+", 2.0, 500.0)
    sw = next(c for c in fake_a.calls if c[0] == "set_waveform")
    assert sw[2] == "Square", f"shape should be 'Square', got {sw[2]!r}"
    print(f"[OK] command_square uses shape 'Square'")

    # command_dc_ramped / command_ac_amplitude_ramped delegate to an attached
    # RampEngine, in generator volts, without touching the hardware directly.
    class _FakeRampEngine:
        def __init__(self):
            self.calls = []
        def retarget(self, label, target_v, mode="offset"):
            self.calls.append((label, target_v, mode))

    try:
        drive.command_dc_ramped("X+", 2.0)
        raise AssertionError("expected RuntimeError with no ramp engine attached")
    except RuntimeError:
        print("[OK] command_dc_ramped requires an attached RampEngine")

    ramp = _FakeRampEngine()
    drive.attach_ramp_engine(ramp)
    drive.command_dc_ramped("X+", 2.5)
    label, target_v, mode = ramp.calls[0]
    assert label == "X+" and mode == "offset"
    assert abs(target_v - 2.5 * 1000.0 / _AMP_GAIN) < 1e-9
    print(f"[OK] command_dc_ramped(X+, 2.5) -> retarget(offset, {target_v:.4f} V)")

    ramp.calls.clear()
    drive.command_ac_amplitude_ramped("X+", 1.5)
    label, target_v, mode = ramp.calls[0]
    assert mode == "amplitude"
    assert abs(target_v - 1.5 * 2.0 * 1000.0 / _AMP_GAIN) < 1e-9
    print(f"[OK] command_ac_amplitude_ramped(X+, 1.5) -> retarget(amplitude, {target_v:.4f} Vpp)")

    print("\n[OK] amp_drive self-test passed")
