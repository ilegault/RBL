"""
video_transcoder.py
Rolling AVI → MP4 transcoder using bundled or system ffmpeg.

Why rolling?  An 8-hour run at record_fps=2 produces a 57 600-frame AVI.
Waiting until Stop to transcode it would block the UI (or a modal dialog)
for minutes.  Instead we transcode each segment as soon as it closes — the
next segment takes ~10 minutes to fill, and at CRF 18 a 10-minute 1080p
segment transcodes in well under a minute on any machine from this decade.
The queue stays shallow.

Graceful degradation
--------------------
If ffmpeg is not found, recording proceeds normally: .avi files accumulate,
session.json records ffmpeg.found = false, the UI says "ffmpeg not found —
keeping .avi only", and nothing raises.  The AVI files are the authoritative
capture artifact; the MP4 is an accessible viewing copy.

ffmpeg search order (find_ffmpeg)
----------------------------------
1. Frozen: next to RBL.exe in the one-folder dist
2. Frozen: in sys._MEIPASS (PyInstaller extraction dir)
3. Dev: <project_root>/tools/ffmpeg.exe
4. shutil.which("ffmpeg") — system PATH
5. None

Command flags
-------------
-y             : overwrite without prompting
-hide_banner   : suppress version banner
-loglevel error: only show errors
-nostdin       : don't consume parent's stdin
-an            : no audio (USB cameras have no audio track)
-movflags +faststart : MP4 index at the start for instant seeking
Write to .mp4.part first; rename to .mp4 only on success so a killed transcode
never leaves a half-written file that looks valid.
"""
import os
import queue
import shutil
import subprocess
import sys

from PySide6.QtCore import QThread, Signal

# ---- ffmpeg discovery ------------------------------------------------------

def find_ffmpeg() -> "str | None":
    """Return the path to ffmpeg, or None if not found anywhere."""
    candidates = []

    if getattr(sys, "frozen", False):
        candidates.append(
            os.path.join(os.path.dirname(sys.executable), "ffmpeg.exe"))
        if hasattr(sys, "_MEIPASS"):
            candidates.append(os.path.join(sys._MEIPASS, "ffmpeg.exe"))
    else:
        # Development tree: <project_root>/tools/ffmpeg.exe
        root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        candidates.append(os.path.join(root, "tools", "ffmpeg.exe"))

    for path in candidates:
        if os.path.isfile(path):
            return path

    return shutil.which("ffmpeg")


def ffmpeg_version(path: str) -> str:
    """Return a short version string from ffmpeg -version, or '' on failure."""
    try:
        flags = {}
        if sys.platform == "win32":
            flags["creationflags"] = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(
            [path, "-version"],
            capture_output=True, text=True, timeout=5, **flags)
        first = result.stdout.splitlines()[0] if result.stdout else ""
        # e.g. "ffmpeg version n6.1 Copyright ..." → "n6.1"
        parts = first.split()
        if len(parts) >= 3:
            return parts[2]
    except Exception:
        pass
    return ""


# ---- Transcode worker thread ------------------------------------------------

class TranscodeQueue(QThread):
    """Single worker thread — one ffmpeg process at a time, FIFO queue.

    Signals
    -------
    finished_one(avi_path, mp4_path, ok, message)
        Emitted after each transcode completes or fails.
    progress(pending_count)
        Emitted when the queue depth changes (after each enqueue or finish).
    """

    finished_one = Signal(str, str, bool, str)  # avi, mp4, ok, message
    progress     = Signal(int)                  # pending job count

    def __init__(self, ffmpeg_path: "str | None", *,
                 crf: int = 18, preset: str = "medium", parent=None):
        super().__init__(parent)
        self._ffmpeg  = ffmpeg_path
        self._crf     = crf
        self._preset  = preset
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._alive   = True

    def enqueue(self, avi_path: str) -> None:
        self._queue.put(avi_path)
        self.progress.emit(self._queue.qsize())

    def stop(self, wait: bool = True) -> None:
        self._alive = False
        self._queue.put(None)   # sentinel to unblock the worker
        if wait:
            self.wait(30_000)   # give current transcode up to 30 s to finish

    def run(self) -> None:
        while self._alive:
            item = self._queue.get()
            if item is None:
                break
            self.progress.emit(self._queue.qsize())
            self._transcode(item)

    def _transcode(self, avi_path: str) -> None:
        mp4_path  = os.path.splitext(avi_path)[0] + ".mp4"
        part_path = mp4_path + ".part"

        if self._ffmpeg is None:
            self.finished_one.emit(avi_path, mp4_path, False,
                                   "ffmpeg not found")
            return

        cmd = [
            self._ffmpeg,
            "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
            "-i", avi_path,
            "-c:v", "libx264",
            "-preset", self._preset,
            "-crf", str(self._crf),
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            "-an",
            # REQUIRED: we write to "<name>.mp4.part", and ffmpeg selects its
            # muxer from the file extension.  ".part" matches no muxer, so
            # without an explicit -f every transcode failed with
            # "Unable to find a suitable output format for '...mp4.part'".
            "-f", "mp4",
            part_path,
        ]

        flags = {}
        if sys.platform == "win32":
            flags["creationflags"] = subprocess.CREATE_NO_WINDOW

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=600, **flags)
            if result.returncode == 0:
                os.replace(part_path, mp4_path)
                self.finished_one.emit(avi_path, mp4_path, True, "ok")
            else:
                # Leave the .avi intact; remove the partial .mp4.
                if os.path.exists(part_path):
                    try:
                        os.remove(part_path)
                    except OSError:
                        pass
                msg = result.stderr.strip() or f"exit code {result.returncode}"
                self.finished_one.emit(avi_path, mp4_path, False, msg)
        except subprocess.TimeoutExpired:
            if os.path.exists(part_path):
                try:
                    os.remove(part_path)
                except OSError:
                    pass
            self.finished_one.emit(avi_path, mp4_path, False, "timeout")
        except Exception as exc:
            self.finished_one.emit(avi_path, mp4_path, False, str(exc))
