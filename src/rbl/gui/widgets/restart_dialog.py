"""
restart_dialog.py
The question asked when automatic cup insertion is started again after a stop.

WHY THIS EXISTS (logging-and-sessions spec C5, ticket 31)
---------------------------------------------------------
While automatic insertion is stopped nothing samples the beam, so the dose total has a
hole. Whether that hole matters depends on one physical fact only the operator knows:
was the beam on the specimen during the pause? Answering "yes" keeps the gap in the
running beam-on time (the specimen was being irradiated, just unsampled); answering "no"
takes it out. A wrong silent default in either direction misstates dpa in a defect-study
write-up, so the beam question has NO preselected answer and OK stays disabled until
the operator chooses one.

The second question is only about the schedule: pick up on the saved boundary (so the
sampling grid does not shift) or start a new period now. Picking up is offered only while
the saved boundary is still in the future; a passed boundary would fire immediately.

This is a plain view: it computes nothing and writes nothing. ``choice()`` hands the
answers back as a ``RestartChoice`` and the tab decides what they mean.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QRadioButton,
    QVBoxLayout,
)

from rbl.services.cup_log import RestartChoice


class RestartDialog(QDialog):
    def __init__(
        self,
        gap_start_text: str,
        now_text: str,
        resume_available: bool,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Restart automatic cup insertion")

        lay = QVBoxLayout(self)

        self.lbl_question = QLabel(
            "Was the beam on the specimen while automatic cup insertion was stopped "
            f"({gap_start_text} to {now_text})?"
        )
        self.lbl_question.setWordWrap(True)
        lay.addWidget(self.lbl_question)

        # Neither radio is checked: the operator must choose.
        self.rb_beam_yes = QRadioButton("Yes, the beam was on")
        self.rb_beam_no = QRadioButton("No, the beam was off")
        beam_group = QButtonGroup(self)
        beam_group.addButton(self.rb_beam_yes)
        beam_group.addButton(self.rb_beam_no)
        lay.addWidget(self.rb_beam_yes)
        lay.addWidget(self.rb_beam_no)

        lay.addSpacing(8)
        lay.addWidget(QLabel("Schedule:"))
        self.rb_resume = QRadioButton("Pick up where you left off")
        self.rb_fresh = QRadioButton("Start a new schedule now")
        schedule_group = QButtonGroup(self)
        schedule_group.addButton(self.rb_resume)
        schedule_group.addButton(self.rb_fresh)
        self.rb_resume.setEnabled(resume_available)
        if resume_available:
            self.rb_resume.setChecked(True)
        else:
            self.rb_resume.setToolTip("The saved schedule boundary has already passed")
            self.rb_fresh.setChecked(True)
        lay.addWidget(self.rb_resume)
        lay.addWidget(self.rb_fresh)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.btn_ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.btn_ok.setText("Start")
        self.btn_ok.setEnabled(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

        beam_group.buttonToggled.connect(self._update_ok)

    def _update_ok(self, *_args) -> None:
        self.btn_ok.setEnabled(self.rb_beam_yes.isChecked() or self.rb_beam_no.isChecked())

    def choice(self) -> RestartChoice | None:
        """The operator's answers, or None while the beam question is unanswered."""
        if not (self.rb_beam_yes.isChecked() or self.rb_beam_no.isChecked()):
            return None
        return RestartChoice(
            resume_schedule=self.rb_resume.isChecked(),
            beam_on_during_gap=self.rb_beam_yes.isChecked(),
        )
