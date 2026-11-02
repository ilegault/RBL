"""
camera_kind_mixin.py
The "which camera?" picker, ZWO row embedding and ZWO Preview plumbing shared
by RecordingPanel (Overview) and CameraTab.

WHY THIS EXISTS
---------------
Both camera views must behave identically for the ZWO camera (spec D11): one
camera at a time, chosen here, with the ZWO rows living in ``ZwoControls``.
Writing the picker twice invites the two screens to disagree about which rows
are visible or what the Open button does, so the logic lives once in this
mixin and each view only supplies its own layout.

The mixin owns no state.  The kind comes from ``recorder.camera_kind()``; a
refused switch (video running, no Capture Process) is made visible by
re-rendering, which puts the combo back.  The Preview is drawn exactly as
decoded (ADR 0008): the application does no image processing on it, and the
caption under the view says the display stretch is not intensity.

Older stand-in recorders without the ZWO API are treated as ``"usb"`` so the
USB path is unchanged (ADR 0008 decision 7).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from rbl.gui import theme
from rbl.gui.widgets.inputs import NoScrollComboBox
from rbl.gui.widgets.zwo_controls import ZwoControls, preview_caption


class CameraKindMixin:
    """Camera picker + ZWO rows. Host must define ``_recorder``, ``_camera``,
    ``_feed``, ``_btn_open``, ``_lbl_res``, ``usb_only_widgets`` and
    ``_render()``.  Deliberately not a QWidget: a second QWidget base next to
    the host's crashes PySide, so the Qt names below are declared for the type
    checker only and resolved from the host at runtime."""

    if TYPE_CHECKING:
        _recorder: Any
        _camera: Any
        _feed: Any
        _btn_open: QPushButton
        _lbl_res: QLabel
        usb_only_widgets: list[QWidget]

        def _render(self) -> None: ...
        def isVisible(self) -> bool: ...

    # ---- construction ------------------------------------------------------

    def _make_kind_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(4)
        row.addWidget(QLabel("Camera:"))
        self.combo_camera_kind = NoScrollComboBox()
        self.combo_camera_kind.addItem("USB camera", "usb")
        self.combo_camera_kind.addItem("ZWO camera", "zwo")
        self.combo_camera_kind.setCurrentIndex(
            self.combo_camera_kind.findData(self._kind()))
        self.combo_camera_kind.currentIndexChanged.connect(self._on_kind_picked)
        row.addWidget(self.combo_camera_kind, stretch=1)
        return row

    def _make_zwo_controls(self) -> ZwoControls:
        self.zwo_controls = ZwoControls(self._recorder)
        return self.zwo_controls

    def _make_preview_caption(self) -> QLabel:
        self.lbl_preview_caption = QLabel("")
        self.lbl_preview_caption.setStyleSheet(
            f"color: {theme.NEUTRAL}; font-size: {theme.FS_CAPTION}px;")
        return self.lbl_preview_caption

    def _connect_zwo_preview(self) -> None:
        sig = getattr(self._recorder, "zwo_preview", None)
        if sig is not None:
            sig.connect(self._on_zwo_preview_frame)

    # ---- model access ------------------------------------------------------

    def _kind(self) -> str:
        getter = getattr(self._recorder, "camera_kind", None)
        return getter() if callable(getter) else "usb"

    def _is_zwo(self) -> bool:
        return self._kind() == "zwo"

    # ---- handlers ----------------------------------------------------------

    def _on_kind_picked(self, _idx: int) -> None:
        kind = self.combo_camera_kind.currentData()
        setter = getattr(self._recorder, "set_camera_kind", None)
        if callable(setter) and kind != self._kind():
            setter(kind)
        # A refusal changes nothing and emits no state change, so render here
        # to put the combo back on the recorder's answer.
        self._render()

    def _on_zwo_preview_frame(self, image: QImage, _fields: dict) -> None:
        if self._is_zwo() and self.isVisible():
            self._draw_zwo_preview(image)

    def _draw_zwo_preview(self, image: QImage) -> None:
        self._feed.set_frame(image)

    def _toggle_zwo_camera(self) -> None:
        if self._recorder.state().get("zwo_open", False):
            self._recorder.disconnect_zwo()
        else:
            self._recorder.connect_zwo()

    # ---- rendering ---------------------------------------------------------

    def _zwo_res_text(self, st: dict) -> str:
        info = st.get("zwo_info") or {}
        if not st.get("zwo_open", False) or not info:
            return ""
        text = (f"{info.get('model', '')} {info.get('serial', '')} · "
                f"{info.get('sensor_width', 0)}x{info.get('sensor_height', 0)}")
        temp = (st.get("zwo_status") or {}).get("sensor_temperature_c")
        if temp is not None:
            text += f" · {temp:.1f} °C"
        return text

    def _render_kind(self, st: dict) -> bool:
        """Sync picker, row visibility, caption and Open label; True if ZWO."""
        zwo = self._kind() == "zwo"
        combo = self.combo_camera_kind
        combo.blockSignals(True)
        combo.setCurrentIndex(combo.findData("zwo" if zwo else "usb"))
        combo.blockSignals(False)

        for w in self.usb_only_widgets:
            w.setVisible(not zwo)
        self.zwo_controls.setVisible(zwo)
        self.lbl_preview_caption.setVisible(zwo)
        if zwo:
            mode = ((st.get("zwo_settings") or {}).get("preview_mode")
                    or self.zwo_controls.combo_preview.currentData() or "auto")
            self.lbl_preview_caption.setText(preview_caption(mode))
            self._btn_open.setText("Close" if st.get("zwo_open", False) else "Open")
            self._lbl_res.setText(self._zwo_res_text(st))
        elif getattr(self, "_shown_kind", "usb") == "zwo":
            # Just switched back: the ZWO text no longer applies.
            self._lbl_res.setText("")
            self._btn_open.setText("Close" if self._camera.is_open() else "Open")
        self._shown_kind = "zwo" if zwo else "usb"
        return zwo
