"""
continue_dialog.py
The question asked when a new session starts and an earlier session left a dose total.

WHY THIS EXISTS (logging-and-sessions spec C6, ticket 32)
---------------------------------------------------------
An irradiation can outlast one session (over a weekend, say). The dose then has to
keep counting from the earlier session's total rather than restart at zero, but only
the operator knows whether this session IS the same irradiation. So it is always asked,
with no time limit. If they continue, a second fact is needed that only they know: was
the beam on the specimen between the earlier session's last cup withdrawal and now? "No"
takes that gap out of the beam-on time; "yes" keeps it. Neither answer is preselected and
OK stays disabled until the beam question is answered, for the same reason as
``restart_dialog``: a silent default misstates dpa.

"Start from zero" needs no beam answer (there is no gap to account for), so choosing it
enables OK immediately. This is a plain view: ``choice()`` hands the answers back as a
``ContinueChoice`` and ``MainWindow`` decides what they mean.
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

from rbl.services.cup_log import ContinueChoice


class ContinueDialog(QDialog):
    def __init__(self, summary_text: str, gap_text: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Continue the dose?")
        lay = QVBoxLayout(self)

        self.lbl_question = QLabel(summary_text)
        self.lbl_question.setWordWrap(True)
        lay.addWidget(self.lbl_question)

        self.rb_continue = QRadioButton("Yes, continue the dose")
        self.rb_zero = QRadioButton("No, start from zero")
        main_group = QButtonGroup(self)
        main_group.addButton(self.rb_continue)
        main_group.addButton(self.rb_zero)
        lay.addWidget(self.rb_continue)
        lay.addWidget(self.rb_zero)

        lay.addSpacing(8)
        self.lbl_gap = QLabel(f"Was the beam on the specimen {gap_text}?")
        self.lbl_gap.setWordWrap(True)
        lay.addWidget(self.lbl_gap)
        self.rb_beam_yes = QRadioButton("Yes, the beam was on")
        self.rb_beam_no = QRadioButton("No, the beam was off")
        beam_group = QButtonGroup(self)
        beam_group.addButton(self.rb_beam_yes)
        beam_group.addButton(self.rb_beam_no)
        lay.addWidget(self.rb_beam_yes)
        lay.addWidget(self.rb_beam_no)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        self.btn_ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.btn_ok.setText("Start session")
        self.btn_ok.setEnabled(False)
        buttons.accepted.connect(self.accept)
        lay.addWidget(buttons)

        main_group.buttonToggled.connect(self._refresh)
        beam_group.buttonToggled.connect(self._refresh)
        self._refresh()

    def _refresh(self, *_args) -> None:
        continuing = self.rb_continue.isChecked()
        self.lbl_gap.setEnabled(continuing)
        self.rb_beam_yes.setEnabled(continuing)
        self.rb_beam_no.setEnabled(continuing)
        beam_answered = self.rb_beam_yes.isChecked() or self.rb_beam_no.isChecked()
        self.btn_ok.setEnabled(self.rb_zero.isChecked() or (continuing and beam_answered))

    def choice(self) -> ContinueChoice | None:
        """The operator's answers, or None while they are incomplete."""
        if self.rb_zero.isChecked():
            return ContinueChoice(continue_dose=False, beam_on_during_gap=False)
        if self.rb_continue.isChecked() and (
            self.rb_beam_yes.isChecked() or self.rb_beam_no.isChecked()
        ):
            return ContinueChoice(
                continue_dose=True, beam_on_during_gap=self.rb_beam_yes.isChecked()
            )
        return None
