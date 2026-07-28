"""
waveform_ring.py
Rolling ring of raw high-rate waveform chunks, one per channel — feeds the HV
Amplifiers tab's "snapshot mode" (the raw-waveform view at narrow time
windows, below where the 10 Hz trend buffers can resolve anything).

Moved out of amp_tab.py so the min/max decimation (subtle enough that it has
already been fixed once for a real bug — see `decimate_minmax`) is testable
without constructing a widget.
"""
import collections

import numpy as np


class WaveformRing:
    """Per-channel ring of (t_end, values) raw window chunks.

    Each stored chunk spans one stream window (nominally `window_duration_s`
    seconds) ending at its `t_end`; sample times are reconstructed on demand
    (see `series`) so the ring only holds the values. Chunks older than
    `keep_seconds + window_duration_s` are dropped as new ones arrive.
    """

    def __init__(self, channels, keep_seconds: float, window_duration_s: float):
        self._chunks = {ch: collections.deque() for ch in channels}
        self._keep_seconds = keep_seconds
        self._window_duration_s = window_duration_s
        # Per-sample time step of the live stream (seconds), taken from the
        # stream payload's sample_period. Chunks are reconstructed as
        # t_start = t_end - n * dt so consecutive windows stitch seamlessly.
        # None until the first payload carrying it arrives; `series` then
        # falls back to the nominal window duration / sample count.
        self._dt: float | None = None
        # Cache of within-window sample-time offsets, keyed by (sample count,
        # sample period). A chunk's absolute sample times are just
        # t_start + template, so this avoids rebuilding np.arange per chunk
        # per frame (that rebuild, over the whole ring every redraw, was the
        # sub-second "caching" lag).
        self._time_templates: dict[tuple, np.ndarray] = {}

    def set_sample_period(self, dt: float | None):
        """Adopt the stream's true sample period when present."""
        if dt:
            self._dt = dt

    def clear(self):
        for dq in self._chunks.values():
            dq.clear()

    def store(self, channel: str, t_end: float, values: np.ndarray):
        """Append a raw window to the ring and drop chunks older than the ring."""
        dq = self._chunks[channel]
        dq.append((t_end, values))
        cutoff = t_end - (self._keep_seconds + self._window_duration_s)
        while dq and dq[0][0] < cutoff:
            dq.popleft()

    def latest_t(self):
        """Most recent raw-window end time across all channels (or None)."""
        best = None
        for dq in self._chunks.values():
            if dq:
                te = dq[-1][0]
                if best is None or te > best:
                    best = te
        return best

    def series(self, channel: str, t_left: float, t_right: float):
        """Concatenated (times, values) of raw samples in [t_left, t_right].

        Returns None if nothing falls in the window.
        """
        dq = self._chunks.get(channel)
        if not dq:
            return None
        ts, vs = [], []
        for t_end, vals in dq:
            n = len(vals)
            if n == 0:
                continue
            # Reconstruct the chunk's start from its OWN sample count and the
            # true sample period, so chunk k's start lands exactly on chunk
            # k-1's end (t_end is sample-accurate upstream).  Assuming a fixed
            # 0.1 s width instead was what let jittery windows overlap/gap and
            # break the stitched trace at each seam.
            dt = self._dt if self._dt else (self._window_duration_s / n)
            t_start = t_end - n * dt
            # Skip chunks that fall entirely outside the visible window — this is
            # what keeps a narrow (few-ms) view from re-scanning the whole ring.
            if t_end < t_left or t_start > t_right:
                continue
            tt = t_start + self._time_template(n, dt)   # cached offsets
            if t_start >= t_left and t_end <= t_right:
                # Whole chunk is inside the view: no masking needed.
                ts.append(tt)
                vs.append(vals)
            else:
                m = (tt >= t_left) & (tt <= t_right)
                if m.any():
                    ts.append(tt[m])
                    vs.append(vals[m])
        if not ts:
            return None
        return np.concatenate(ts), np.concatenate(vs)

    def _time_template(self, n: int, dt: float) -> np.ndarray:
        """Sample-centre time offsets for an n-sample window at step *dt*.

        Absolute sample times are ``t_start + template``.  The offsets depend
        only on the sample count and the sample period (both constant per
        profile), so caching them by ``(n, dt)`` avoids rebuilding
        ``np.arange`` for every chunk on every redraw.
        """
        key  = (n, dt)
        tmpl = self._time_templates.get(key)
        if tmpl is None:
            tmpl = (np.arange(n) + 0.5) * dt
            self._time_templates[key] = tmpl
        return tmpl


class AlignedWaveHistory:
    """Recent raw windows for several channels, kept aligned by SAMPLE INDEX.

    `WaveformRing` above answers "what did this channel do between t1 and t2".
    This answers a different question — "give me the last N samples of these
    channels, on one grid" — and the difference matters: the Overview's pair
    trace is a comparison, so its two series must be the same samples, not two
    time-masked selections that a rounding could leave one sample apart.

    Windows are pushed whole and stored whole. A tail is only ever built from a
    CONSECUTIVE run of windows in which every requested channel was present, so
    a profile switch that drops a channel (or a paused one) truncates the run
    instead of silently splicing across the gap.
    """

    def __init__(self, max_samples: int):
        self._windows: collections.deque = collections.deque()
        self._max_samples = int(max_samples)
        self._samples = 0        # longest channel's total, the memory bound

    def clear(self):
        self._windows.clear()
        self._samples = 0

    def push(self, window: dict):
        """Store one stream window: {channel -> raw sample array}.

        Channels absent from *window* are absent for that window, which is what
        breaks the run for anything asking about them.
        """
        window = {ch: np.asarray(v, dtype=float)
                  for ch, v in window.items() if v is not None and len(v)}
        if not window:
            # An empty window still breaks continuity: pushing nothing would
            # let the next window stitch straight onto a stale one. One marker
            # is enough — a profile that never samples these channels would
            # otherwise pile up empty windows at the stream rate forever.
            if not self._windows or self._windows[-1]:
                self._windows.append({})
            return
        self._windows.append(window)
        self._samples += max(len(v) for v in window.values())
        while self._windows and self._samples > self._max_samples:
            oldest = self._windows.popleft()
            if oldest:
                self._samples -= max(len(v) for v in oldest.values())

    def _run(self, channels) -> list:
        """Newest-first list of windows in which every channel is present."""
        run = []
        for window in reversed(self._windows):
            if any(ch not in window for ch in channels):
                break
            run.append(window)
        return run

    def aligned_length(self, channels) -> int:
        """Samples available to all of *channels* on one contiguous grid."""
        return sum(len(w[channels[0]]) for w in self._run(channels))

    def aligned_tail(self, channels, n: int) -> dict:
        """{channel -> last min(n, available) samples}, one shared grid.

        Empty dict if the channels have no common history yet.
        """
        run = self._run(channels)
        if not run or n <= 0:
            return {}
        take, total = [], 0
        for window in run:                     # newest first
            take.append(window)
            total += len(window[channels[0]])
            if total >= n:
                break
        take.reverse()                         # back into time order
        out = {}
        for ch in channels:
            joined = np.concatenate([w[ch] for w in take])
            out[ch] = joined[-n:] if joined.size > n else joined
        return out


def decimate_minmax(x: np.ndarray, y: np.ndarray, max_points: int):
    """Envelope-preserving decimation: bin the series and keep min+max/bin.

    Plain striding would drop transient peaks between samples; min/max
    binning keeps the visible envelope while capping the point count so a
    100 kS/s window redraws cheaply.

    Each bin's two extrema are emitted AT THEIR REAL SAMPLE POSITIONS, in
    the order they occur in time (earliest first).  The old code drew both
    at the bin's left-edge x with min always before max: at a sharp tip
    (e.g. a triangle apex) that collapsed the peak onto a vertical segment
    and, on a falling edge where the max precedes the min, drew the pair
    backwards — the "somersault"/flip seen on the waveform tips.  Placing
    each extreme at its own x in temporal order reproduces the true up/down
    shape of every peak.
    """
    n = len(x)
    if n <= max_points:
        return x, y
    bins = max(1, max_points // 2)
    usable = (n // bins) * bins
    if usable < bins:
        return x, y
    xb = x[:usable].reshape(bins, -1)
    yb = y[:usable].reshape(bins, -1)
    cols  = np.arange(bins)
    i_min = yb.argmin(axis=1)
    i_max = yb.argmax(axis=1)
    # Per bin, order the two extrema by their in-bin (time) index so the
    # emitted x stays monotonic and the peak is drawn the right way round.
    i_first = np.minimum(i_min, i_max)
    i_last  = np.maximum(i_min, i_max)
    x_out = np.empty(bins * 2, dtype=float)
    y_out = np.empty(bins * 2, dtype=float)
    x_out[0::2] = xb[cols, i_first]
    x_out[1::2] = xb[cols, i_last]
    y_out[0::2] = yb[cols, i_first]
    y_out[1::2] = yb[cols, i_last]
    # Keep the un-binned tail so the live right edge is never truncated
    # (the reshape drops the final < bins samples otherwise).
    if usable < n:
        x_out = np.concatenate([x_out, x[usable:]])
        y_out = np.concatenate([y_out, y[usable:]])
    return x_out, y_out
