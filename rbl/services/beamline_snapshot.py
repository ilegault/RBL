"""
beamline_snapshot.py
Decouples the session recorder from the GUI layer.

The old logger_widget.py relied on OverviewTab._camera_metadata() to get its
data — a service depending on a GUI tab.  That made the logger unusable
without an Overview tab instance and impossible to test in isolation.

This class subscribes to Beamline's signals directly, caches the latest state
of each subsystem, and exposes snapshot() -> dict.  No QWidget needed; no tab
needed.  The session recorder calls snapshot() on its CSV timer; the result
is the same dict that _camera_metadata() used to produce, but vacuum is now
included (the old logger silently omitted it) and the class lives in services/
where it belongs.

Threading: snapshot() is called from the GUI thread (the CSV timer runs
there).  All signal callbacks also run on the GUI thread because Beamline
emits from there.  No locking needed.
"""
import dataclasses

from PySide6.QtCore import QObject

from rbl.state.beamline import Beamline


def _json_safe(obj):
    """Recursively make a dataclasses.asdict() result JSON-serializable.

    Handles three cases that asdict() leaves as raw Python objects:

    * numpy arrays  — window_kv / window_ma in AmpChannelSnapshot are stripped
      entirely (they are large raw buffers meant for the live scope, not for
      sidecars); any other ndarray falls back to .tolist().
    * Plain-class instances (e.g. BeamEstimate) — converted to a dict of their
      instance attributes so the beam-position estimate survives in the JSON.
    * IEEE special floats — NaN/Inf survive json.dump() only on some platforms;
      they are left as-is here and callers should use default=str if needed.
    """
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()
                if k not in ("window_kv", "window_ma")}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    # numpy arrays that slipped through (shouldn't happen after the key filter
    # above, but guard anyway so we never raise on serialisation)
    if type(obj).__name__ == "ndarray":
        return obj.tolist()
    # Plain (non-dataclass) class instance — e.g. BeamEstimate
    if (hasattr(obj, "__dict__")
            and not isinstance(obj, (bool, int, float, str, bytes, type))):
        return {k: _json_safe(v) for k, v in vars(obj).items()}
    return obj


class BeamlineSnapshotProvider(QObject):
    """Cache the latest snapshot of every beamline subsystem.

    Connects to all six Beamline signals at construction time; thereafter
    snapshot() returns a consistent dict without touching any hardware.
    """

    def __init__(self, beamline: Beamline, parent=None):
        super().__init__(parent)
        self._latest: dict = {
            "motors":   {},
            "logamps":  {},
            "amps":     {},
            "funcgens": {},
            "scope":    {},
            "vacuum":   {},
        }

        beamline.motors_changed.connect(
            lambda s: self._store("motors", s))
        beamline.logamps_changed.connect(
            lambda s: self._store("logamps", s))
        beamline.amps_changed.connect(
            lambda s: self._store("amps", s))
        beamline.funcgens_changed.connect(
            lambda s: self._store("funcgens", s))
        beamline.scope_changed.connect(
            lambda s: self._store("scope", s))
        beamline.vacuum_changed.connect(
            lambda s: self._store("vacuum", s))

    # ---- internal ----------------------------------------------------------

    def _store(self, key: str, obj) -> None:
        if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
            self._latest[key] = _json_safe(dataclasses.asdict(obj))
        else:
            self._latest[key] = {}

    # ---- public API --------------------------------------------------------

    def snapshot(self) -> dict:
        """Return a copy of the latest beamline state as a plain dict.

        Each top-level key ("motors", "logamps", "amps", "funcgens", "scope",
        "vacuum") holds {} until that subsystem has reported at least once.
        The returned dict is a shallow copy of the top level; callers must not
        mutate the nested dicts.
        """
        return dict(self._latest)
