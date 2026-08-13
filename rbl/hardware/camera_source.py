"""
camera_source.py
Thread-safe USB camera source — the sole owner of cv2.VideoCapture.

The old camera_widget.py called cap.read() on the GUI thread: a stalled USB
read froze the whole application.  This class moves the grab loop to a QThread
so the GUI stays responsive regardless of USB latency.

Two signal streams are emitted:
  frame_ready   — every successful read, with a monotonic timestamp taken
                  immediately after read() returns.  Consumers (VideoRecorder,
                  CameraTab) decide independently what to do with each frame.
  preview_ready — throttled to PREVIEW_EMIT_MAX_FPS so the Qt event queue is
                  not flooded with 6 MB arrays at 30 Hz for 8 hours.

If cv2 is not installed the class still exists; open() returns False and emits
a clear error() so the rest of the application continues without a camera.

Threading model
---------------
  GUI thread  : open(), close(), is_open(), latest_frame(), actual_size()
  Camera thread : _CameraThread.run() — grab loop, VideoWriter, frames.csv
  Signals cross the thread boundary as queued connections (Qt default).
"""
import sys
import time

from PySide6.QtCore import QObject, QThread, Signal

from rbl.config.recording_config import PREVIEW_EMIT_MAX_FPS

try:
    import cv2
    _CV2_OK = True
except ImportError:
    _CV2_OK = False

_SLEEP_CHUNK_S = 0.05   # stop-check granularity while the thread is idle
_MAX_CONSECUTIVE_FAILURES = 30


# ---- Worker thread ---------------------------------------------------------

class _CameraThread(QThread):
    frame_ready   = Signal(object, float)   # BGR ndarray, t_mono
    preview_ready = Signal(object)          # BGR ndarray, throttled
    opened        = Signal(int, int, float) # width, height, actual_fps
    closed        = Signal()
    error         = Signal(str)

    def __init__(self, index: int, width: int, height: int, fps: int, parent=None):
        super().__init__(parent)
        self._index  = index
        self._width  = width
        self._height = height
        self._fps    = fps
        self._stop   = False
        self._latest_frame = None
        self._actual_w: int   = 0
        self._actual_h: int   = 0
        self._actual_fps: float = 0.0

    def stop(self) -> None:
        self._stop = True

    def latest_frame(self):
        return self._latest_frame

    def actual_size(self) -> tuple[int, int]:
        return self._actual_w, self._actual_h

    def actual_fps(self) -> float:
        return self._actual_fps

    def run(self) -> None:
        backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
        cap = cv2.VideoCapture(self._index, backend)
        if not cap.isOpened():
            self.error.emit(f"Camera {self._index}: could not open")
            return

        # Request MJPEG — many USB cameras can only stream HD as MJPEG.
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))

        req_w = self._width if self._width > 0 else 10000
        req_h = self._height if self._height > 0 else 10000
        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  req_w)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, req_h)
        cap.set(cv2.CAP_PROP_FPS, self._fps)

        w   = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h   = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS) or float(self._fps)

        self._actual_w   = w
        self._actual_h   = h
        self._actual_fps = fps
        self.opened.emit(w, h, fps)

        failures    = 0
        prev_emit_t = 0.0
        min_preview_interval = 1.0 / PREVIEW_EMIT_MAX_FPS

        while not self._stop:
            ret, frame = cap.read()
            t_mono = time.perf_counter()

            if not ret:
                failures += 1
                if failures >= _MAX_CONSECUTIVE_FAILURES:
                    self.error.emit("camera lost")
                    break
                # Brief sleep to avoid spinning on a slow camera.
                time.sleep(min(_SLEEP_CHUNK_S, 0.033))
                continue
            failures = 0

            self._latest_frame = frame
            self.frame_ready.emit(frame, t_mono)

            if t_mono - prev_emit_t >= min_preview_interval:
                self.preview_ready.emit(frame)
                prev_emit_t = t_mono

        cap.release()
        self._latest_frame = None
        self.closed.emit()


# ---- Public facade ---------------------------------------------------------

class CameraSource(QObject):
    """Single-process owner of one USB camera.

    Both the Overview RecordingPanel and the Camera tab connect to this object;
    there is exactly one cv2.VideoCapture per process.  Neither view may open
    the camera themselves — they call open()/close() here.
    """

    frame_ready   = Signal(object, float)   # BGR ndarray, t_mono (perf_counter)
    preview_ready = Signal(object)          # BGR ndarray, throttled for display
    opened        = Signal(int, int, float) # width, height, actual fps
    closed        = Signal()
    error         = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thread: _CameraThread | None = None

    # ---- public API --------------------------------------------------------

    def open(self, index: int, width: int, height: int, fps: int) -> bool:
        """Start the camera grab loop.  Returns False immediately if cv2 is absent."""
        if not _CV2_OK:
            self.error.emit("opencv-python not installed — camera unavailable")
            return False
        if self._thread is not None:
            return True   # already open

        t = _CameraThread(index, width, height, fps)
        t.frame_ready.connect(self.frame_ready)
        t.preview_ready.connect(self.preview_ready)
        t.opened.connect(self.opened)
        t.error.connect(self._on_thread_error)
        t.closed.connect(self._on_thread_closed)
        t.finished.connect(t.deleteLater)
        self._thread = t
        t.start()
        return True

    def close(self) -> None:
        if self._thread is None:
            return
        self._thread.stop()
        self._thread.wait(3000)   # up to 3 s; USB read should unblock quickly
        self._thread = None

    def is_open(self) -> bool:
        return self._thread is not None

    def latest_frame(self):
        """Return the most recent BGR frame, or None if camera is not open."""
        if self._thread is None:
            return None
        return self._thread.latest_frame()

    def actual_size(self) -> tuple[int, int]:
        if self._thread is None:
            return 0, 0
        return self._thread.actual_size()

    def actual_fps(self) -> float:
        if self._thread is None:
            return 0.0
        return self._thread.actual_fps()

    # ---- internal ----------------------------------------------------------

    def _on_thread_error(self, msg: str) -> None:
        self._thread = None
        self.error.emit(msg)
        self.closed.emit()

    def _on_thread_closed(self) -> None:
        self._thread = None
        self.closed.emit()
