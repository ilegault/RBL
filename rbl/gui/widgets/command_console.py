"""
command_console.py
Shared pieces of a hardware command console: a history-aware line edit and a
timestamped read-only log pane.

Used by the motor tab's Galil command console and the function-generator
tab's SCPI console — both are a line edit feeding commands to an instrument,
echoed into a read-only log with a timestamp.
"""
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QKeyEvent
from PySide6.QtWidgets import QLineEdit, QTextEdit


class HistoryLineEdit(QLineEdit):
    """QLineEdit with Up/Down arrow key command history."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._history: list[str] = []
        self._history_idx: int = -1   # -1 = not browsing history
        self._current_draft: str = ""

    def add_to_history(self, cmd: str):
        if cmd and (not self._history or self._history[-1] != cmd):
            self._history.append(cmd)
        self._history_idx = -1
        self._current_draft = ""

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Up:
            if not self._history:
                return
            if self._history_idx == -1:
                self._current_draft = self.text()
                self._history_idx = len(self._history) - 1
            elif self._history_idx > 0:
                self._history_idx -= 1
            self.setText(self._history[self._history_idx])
            self.end(False)
        elif event.key() == Qt.Key.Key_Down:
            if self._history_idx == -1:
                return
            if self._history_idx < len(self._history) - 1:
                self._history_idx += 1
                self.setText(self._history[self._history_idx])
            else:
                self._history_idx = -1
                self.setText(self._current_draft)
            self.end(False)
        else:
            if self._history_idx != -1:
                # any other key resets browsing
                self._history_idx = -1
            super().keyPressEvent(event)


class LogPane(QTextEdit):
    """Read-only, monospaced, timestamped command/response log."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(QFont("Consolas", 9))

    def log(self, line: str):
        ts = time.strftime("%H:%M:%S")
        self.append(f"[{ts}] {line}")
