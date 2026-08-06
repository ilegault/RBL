"""
raw_capture_writer.py
One .npz file per (test, level, target AIN) for the amplifier test matrix.

SCOPE
-----
Accumulates window arrays in memory and writes once at close_capture().
This avoids the np.append-in-a-loop reallocation that would visibly stall
the GUI at 100 kS/s within seconds.

Each .npz contains:
  samples  - 1-D float32 array of raw ADC voltages
  meta     - 0-d object array wrapping a metadata dict

Filename convention:
  {test_id}__{level_slug}__{ain}__{seq:03d}.npz
where level_slug = lower(re.sub(r'[^a-z0-9]+', '_', level_label)).
"""
import re
import time
import logging
from pathlib import Path
from datetime import timezone, datetime

import numpy as np

from rbl.config.amp_test_config import RAW_DTYPE, RAW_MAX_SAMPLES_PER_CAPTURE

log = logging.getLogger(__name__)


class RawCaptureOverflow(Exception):
    """Raised by RawCaptureWriter.append when the per-capture sample cap
    would be exceeded.  The runner must catch this, close_capture() cleanly,
    record a truncation flag in the CSV, and continue — a truncated capture
    is data; a crashed run is not."""


def _slugify(text: str) -> str:
    """Lower-case, replace non-alphanumerics with underscores, strip leading/
    trailing underscores, collapse runs of underscores."""
    s = text.lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = s.strip("_")
    return s or "x"


class RawCaptureWriter:
    """One .npz per (test, level, target AIN).  Accumulates window arrays in
    memory, writes once at close_capture()."""

    def __init__(self, run_dir: Path, test_id: str):
        self._run_dir  = Path(run_dir)
        self._test_id  = test_id
        self._raw_dir  = self._run_dir / "raw"
        self._raw_dir.mkdir(parents=True, exist_ok=True)

        # per-capture state
        self._windows:       list[np.ndarray] = []
        self._n_samples:     int  = 0
        self._open:          bool = False
        self._level_label:   str  = ""
        self._ain:           str  = ""
        self._seq:           int  = 0
        self._sample_period: float = 0.0
        self._metadata:      dict  = {}
        self._t0_iso:        str  = ""

        self._bytes_written: int = 0

    # ------------------------------------------------------------------
    # Capture lifecycle
    # ------------------------------------------------------------------

    def open_capture(self, level_label: str, ain: str, seq: int,
                     sample_period: float, metadata: dict) -> None:
        """Begin a new capture.  Must be paired with close_capture() or abort_capture()."""
        if self._open:
            raise RuntimeError("open_capture called while a capture is already open")
        self._windows       = []
        self._n_samples     = 0
        self._open          = True
        self._level_label   = level_label
        self._ain           = ain
        self._seq           = seq
        self._sample_period = sample_period
        self._metadata      = dict(metadata)
        self._t0_iso        = datetime.now(tz=timezone.utc).isoformat()

    def append(self, samples: np.ndarray) -> None:
        """Accumulate one window's waveform array.

        Raises RawCaptureOverflow before adding samples that would push the
        running total past RAW_MAX_SAMPLES_PER_CAPTURE.
        """
        if not self._open:
            raise RuntimeError("append called with no open capture")
        if self._n_samples + len(samples) > RAW_MAX_SAMPLES_PER_CAPTURE:
            raise RawCaptureOverflow(
                f"capture for {self._test_id}/{self._ain} at level "
                f"{self._level_label!r} would exceed "
                f"{RAW_MAX_SAMPLES_PER_CAPTURE:,} samples"
            )
        self._windows.append(np.asarray(samples, dtype=RAW_DTYPE))
        self._n_samples += len(samples)

    def close_capture(self) -> str:
        """Concatenate all windows and write the .npz.

        Returns the written file path, or "" if no samples were accumulated.
        """
        if not self._open:
            raise RuntimeError("close_capture called with no open capture")
        self._open = False

        if not self._windows:
            return ""

        combined = np.concatenate(self._windows).astype(RAW_DTYPE)
        slug     = _slugify(self._level_label)
        fname    = f"{self._test_id}__{slug}__{self._ain}__{self._seq:03d}.npz"
        path     = self._raw_dir / fname

        meta = dict(self._metadata)
        meta.update({
            "t0_iso":        self._t0_iso,
            "n_samples":     int(combined.size),
            "sample_period": self._sample_period,
            "sample_rate_hz": (1.0 / self._sample_period) if self._sample_period else 0.0,
        })

        meta_arr = np.empty((), dtype=object)
        meta_arr[()] = meta

        np.savez_compressed(str(path), samples=combined, meta=meta_arr)
        nbytes = path.stat().st_size
        self._bytes_written += nbytes
        log.debug("raw capture: %s (%d samples, %d bytes)", fname, combined.size, nbytes)

        self._windows   = []
        self._n_samples = 0
        return str(path)

    def abort_capture(self) -> None:
        """Discard accumulated data without writing.  Deletes nothing."""
        self._open      = False
        self._windows   = []
        self._n_samples = 0

    # ------------------------------------------------------------------
    # Accounting
    # ------------------------------------------------------------------

    @property
    def bytes_written(self) -> int:
        """Total compressed bytes flushed to disk across all captures."""
        return self._bytes_written


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import tempfile, os

    print("=== raw_capture_writer self-test ===")

    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp)
        w = RawCaptureWriter(run_dir, test_id="G1.1")

        # --- round-trip ---
        meta_in = {
            "test_id": "G1.1", "level_label": "3 kV", "level_value": 3.0,
            "ain": "AIN9", "amp_label": "Y+", "kind": "voltage",
            "profile": "WAVEFORM", "commanded_kv": 3.0, "driven_amp": "Y+",
            "run_id": "test_run_001", "front_panel_hash": "abcd1234",
        }
        w.open_capture("3 kV", "AIN9", 0, sample_period=8e-5, metadata=meta_in)
        rng = np.random.default_rng(42)
        windows = [rng.random(10_000, dtype=np.float64) for _ in range(12)]
        for arr in windows:
            w.append(arr)
        path = w.close_capture()
        assert path.endswith(".npz"), f"expected .npz path, got {path!r}"

        # slug check
        fname = os.path.basename(path)
        assert "3_kv" in fname, f"slug not found in filename: {fname}"
        assert "AIN9" in fname, f"AIN not in filename: {fname}"
        assert fname.endswith("000.npz"), f"seq not 000 in filename: {fname}"
        print(f"[OK] filename slug: {fname}")

        # reload and bit-identity check
        expected = np.concatenate(windows).astype(np.float32)
        with np.load(path, allow_pickle=True) as data:
            assert np.array_equal(data["samples"], expected), "round-trip mismatch"
            print("[OK] samples round-trip bit-identical after float32 cast")

            loaded_meta = data["meta"].item()
            assert loaded_meta["test_id"] == "G1.1"
            assert loaded_meta["level_label"] == "3 kV"
            assert loaded_meta["n_samples"] == 120_000
            print("[OK] metadata round-trips correctly")

        # bytes_written
        assert w.bytes_written > 0
        print(f"[OK] bytes_written = {w.bytes_written:,}")

        # --- overflow cap ---
        w2 = RawCaptureWriter(run_dir, test_id="G1.2")
        w2.open_capture("5 kV", "AIN8", 0, sample_period=1e-5,
                        metadata={"test_id": "G1.2", "level_label": "5 kV"})
        # append up to just below the cap
        cap = RAW_MAX_SAMPLES_PER_CAPTURE
        chunk = np.zeros(cap, dtype=np.float32)
        w2.append(chunk)
        try:
            w2.append(np.zeros(1, dtype=np.float32))
            assert False, "should have raised RawCaptureOverflow"
        except RawCaptureOverflow as exc:
            print(f"[OK] RawCaptureOverflow raised: {exc}")

        # already-appended data is still recoverable
        p2 = w2.close_capture()
        with np.load(p2, allow_pickle=True) as data2:
            assert data2["samples"].size == cap
            print(f"[OK] truncated capture recoverable ({data2['samples'].size:,} samples)")

        # --- empty capture returns "" ---
        w3 = RawCaptureWriter(run_dir, test_id="G3.1")
        w3.open_capture("quiescent", "AIN7", 0, 8e-5, {})
        result = w3.close_capture()
        assert result == "", f"expected empty string, got {result!r}"
        print("[OK] empty capture returns ''")

    print("\n[OK] raw_capture_writer self-test passed")
