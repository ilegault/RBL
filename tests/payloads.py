"""
Stream-window builders for tests that drive the hardware tabs.

Both tabs used to be fed a flat {AIN: volts} dict straight into a private
method on the widget (`_on_reading` / `_on_window`), which meant the test was
exercising a conversion that lived in the widget. It doesn't any more: volts
become amps, kilovolts and milliamps once, in Beamline, and the tabs render
the typed snapshots that come out (see rbl/state/labjack_link.py).

So a test that wants to put a voltage on a screen now goes the way the app
goes — build the window the stream worker would emit, hand it to a real
Beamline, let the snapshot land on the tab. `LabJackFeed` wires exactly the
connections MainWindow makes, so what these tests cover is the production
path rather than a widget-shaped imitation of it.
"""
import numpy as np

from rbl.config import hardware_config as SC
from rbl.state.beamline import Beamline

# A single window covering every channel, as the shared stream worker emits it.
# Channel map (rbl/config/hardware_config.py): log amps AIN0-3, spare AIN4-5,
# then amp monitors as (current, voltage) pairs Y-(6,7) Y+(8,9) X-(10,11) X+(12,13).
FULL_READING = {
    "AIN0": 3.0, "AIN1": 3.0, "AIN2": 3.0, "AIN3": 3.0,   # log amps -> 1 µA each
    "AIN6":  0.5, "AIN7":  -2.0,     # Y- :  5 mA, -2 kV
    "AIN8":  0.5, "AIN9":   2.0,     # Y+ :  5 mA,  2 kV
    "AIN10": 1.0, "AIN11": -3.0,     # X- : 10 mA, -3 kV
    "AIN12": 1.0, "AIN13":  3.0,     # X+ : 10 mA,  3 kV
}


def window_payload(volts: dict, t: float = 1.0, samples: int = 8,
                   sample_period: float = None, waveforms: dict = None,
                   profile: str = "FULL") -> dict:
    """One LabJackStreamWorker window, built from a flat {AIN: volts} dict.

    Log-amp channels carry a window mean; amplifier channels carry the full
    waveform plus the peak / pk-pk / RMS scalars the worker derives from it.
    An AIN absent from *volts* (and from *waveforms*) is None — the same
    "not in this profile's scan list" marker the worker emits, which is what
    a paused readout is rendered from.

    Pass *waveforms* to give an amplifier channel real samples instead of a
    DC level; `samples` sets the window length for the flat case.
    """
    waveforms = waveforms or {}
    channels = {}
    length = samples

    for ain in SC.LABJACK_CHANNEL_MAP:
        v = volts.get(ain)
        channels[ain] = None if v is None else {"mean": float(v)}

    for ain in SC.AMP_AIN_NAMES:
        wave = waveforms.get(ain)
        if wave is None:
            v = volts.get(ain)
            if v is None:
                channels[ain] = None
                continue
            wave = np.full(samples, float(v), dtype=float)
        else:
            wave = np.asarray(wave, dtype=float)
        length = len(wave)
        with np.errstate(invalid="ignore", divide="ignore"):
            pk = float(np.max(np.abs(wave)))
            pkpk = float(wave.max() - wave.min())
            rms = float(np.sqrt(np.mean(wave ** 2)))
        channels[ain] = {
            "waveform": wave.copy(),
            "peak":  pk,
            "pk_pk": pkpk,
            "rms":   rms,
        }

    payload = {"profile": profile, "window_samples": length, "t": t,
               "channels": channels}
    if sample_period is not None:
        payload["sample_period"] = sample_period
    return payload


class LabJackFeed:
    """A real Beamline wired to the tabs under test, as MainWindow wires it.

    One feed can drive both tabs at once, which is what makes the
    non-interference tests meaningful: they assert that ONE window reaches
    both screens and each takes only its own channels out of it.
    """

    def __init__(self, *tabs, beamline: Beamline = None):
        self.beamline = beamline or Beamline()
        for tab in tabs:
            if hasattr(tab, "on_logamp_state"):
                self.beamline.logamps_changed.connect(tab.on_logamp_state)
            if hasattr(tab, "on_amp_state"):
                self.beamline.amps_changed.connect(tab.on_amp_state)
            if hasattr(tab, "on_window"):
                self.beamline.raw_window_ready.connect(tab.on_window)
            if hasattr(tab, "on_labjack_connected"):
                self.beamline.labjack_connected.connect(tab.on_labjack_connected)
            if hasattr(tab, "on_labjack_disconnected"):
                self.beamline.labjack_disconnected_evt.connect(tab.on_labjack_disconnected)
            if hasattr(tab, "on_labjack_error"):
                self.beamline.stream_error.connect(tab.on_labjack_error)
            if hasattr(tab, "on_profile_changed"):
                self.beamline.profile_changed.connect(tab.on_profile_changed)

    def send(self, volts: dict, t: float = 1.0, **kwargs):
        """Build a window from {AIN: volts} and push it through Beamline."""
        self.send_payload(window_payload(volts, t=t, **kwargs))

    def send_payload(self, payload: dict, active_profile: str = ""):
        self.beamline.raw_window_ready.emit(payload)
        self.beamline.ingest_labjack_window(payload, active_profile=active_profile)

