"""
tests/test_visa_probe.py
Tests for scripts/visa_probe.py:
- run_child reports RESOURCE: only when an instrument answers *IDN? with a non-empty string.
- resources that list but fail to open or query are reported as LISTED BUT DID NOT ANSWER.
- child output parser extracts only the resource identifier (not the trailing IDN).
- main returns exit code 2 with the troubleshooting banner if no backend got an answer.
- summary deduplicates resources across multiple backends and formats answered counts.
"""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import scripts.visa_probe as vp


def test_parse_child_found_extracts_resource_name():
    child_output = (
        "  library: Visa Library\n"
        "  1 resource(s):\n"
        "    RESOURCE: GPIB0::2::INSTR KEITHLEY INSTRUMENTS INC.,MODEL 6482,4008420,A01\n"
        "    GPIB0::2::INSTR protocol: SCPI (MEP enabled)\n"
    )
    found = vp.parse_child_found(child_output)
    assert found == ["GPIB0::2::INSTR"]


def test_parse_child_found_ignores_listed_but_not_answered():
    child_output = (
        "  library: Visa Library\n"
        "  1 resource(s):\n"
        "    GPIB0::14::INSTR: LISTED BUT DID NOT ANSWER (VI_ERROR_RSRC_NFOUND)\n"
    )
    found = vp.parse_child_found(child_output)
    assert found == []


def test_run_child_when_resource_answers(capsys):
    mock_rm = MagicMock()
    mock_rm.visalib = "MockVisaLib"
    mock_rm.list_resources.return_value = ("GPIB0::2::INSTR",)

    mock_inst = MagicMock()
    mock_inst.query.side_effect = lambda cmd: (
        "KEITHLEY INSTRUMENTS INC.,MODEL 6482,4008420,A01" if cmd == "*IDN?" else "1"
    )
    mock_rm.open_resource.return_value = mock_inst

    mock_pyvisa = MagicMock()
    mock_pyvisa.ResourceManager.return_value = mock_rm

    with patch.dict("sys.modules", {"pyvisa": mock_pyvisa}):
        ret = vp.run_child("")

    assert ret == 0
    captured = capsys.readouterr().out
    assert "RESOURCE: GPIB0::2::INSTR KEITHLEY INSTRUMENTS INC.,MODEL 6482,4008420,A01" in captured
    assert "protocol: SCPI (MEP enabled)" in captured
    mock_inst.close.assert_called_once()


def test_run_child_when_open_fails(capsys):
    mock_rm = MagicMock()
    mock_rm.visalib = "MockVisaLib"
    mock_rm.list_resources.return_value = ("GPIB0::14::INSTR",)
    mock_rm.open_resource.side_effect = RuntimeError("VI_ERROR_RSRC_NFOUND: resource not found")

    mock_pyvisa = MagicMock()
    mock_pyvisa.ResourceManager.return_value = mock_rm

    with patch.dict("sys.modules", {"pyvisa": mock_pyvisa}):
        ret = vp.run_child("")

    assert ret != 0
    captured = capsys.readouterr().out
    assert "RESOURCE:" not in captured
    expected_msg = (
        "GPIB0::14::INSTR: LISTED BUT DID NOT ANSWER (VI_ERROR_RSRC_NFOUND: resource not found)"
    )
    assert expected_msg in captured


def test_run_child_when_idn_query_fails(capsys):
    mock_rm = MagicMock()
    mock_rm.visalib = "MockVisaLib"
    mock_rm.list_resources.return_value = ("GPIB0::2::INSTR",)

    mock_inst = MagicMock()
    mock_inst.query.side_effect = RuntimeError("VI_ERROR_TMO: timeout expired")
    mock_rm.open_resource.return_value = mock_inst

    mock_pyvisa = MagicMock()
    mock_pyvisa.ResourceManager.return_value = mock_rm

    with patch.dict("sys.modules", {"pyvisa": mock_pyvisa}):
        ret = vp.run_child("")

    assert ret != 0
    captured = capsys.readouterr().out
    assert "RESOURCE:" not in captured
    assert "GPIB0::2::INSTR: LISTED BUT DID NOT ANSWER (VI_ERROR_TMO: timeout expired)" in captured
    mock_inst.close.assert_called_once()


def test_run_child_when_idn_response_is_empty(capsys):
    mock_rm = MagicMock()
    mock_rm.visalib = "MockVisaLib"
    mock_rm.list_resources.return_value = ("GPIB0::2::INSTR",)

    mock_inst = MagicMock()
    mock_inst.query.return_value = "   \n"
    mock_rm.open_resource.return_value = mock_inst

    mock_pyvisa = MagicMock()
    mock_pyvisa.ResourceManager.return_value = mock_rm

    with patch.dict("sys.modules", {"pyvisa": mock_pyvisa}):
        ret = vp.run_child("")

    assert ret != 0
    captured = capsys.readouterr().out
    assert "RESOURCE:" not in captured
    assert "GPIB0::2::INSTR: LISTED BUT DID NOT ANSWER (empty *IDN? response)" in captured
    mock_inst.close.assert_called_once()


def test_main_exit_code_2_when_no_backend_answers(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["visa_probe.py"])
    monkeypatch.setattr(vp, "BACKEND_CANDIDATES", [("Mock Backend", "")])

    dummy_proc = subprocess.CompletedProcess(
        args=["python", "visa_probe.py", "--backend", ""],
        returncode=1,
        stdout=(
            "  1 resource(s):\n"
            "    GPIB0::14::INSTR: LISTED BUT DID NOT ANSWER (VI_ERROR_RSRC_NFOUND)\n"
        ),
        stderr="",
    )

    with patch("subprocess.run", return_value=dummy_proc):
        ret = vp.main()

    assert ret == 2
    captured = capsys.readouterr().out
    assert "NO BACKEND GOT AN ANSWER. Listed resources did not respond to *IDN?." in captured
    assert "Check: instrument powered, GPIB selected (not RS-232)" in captured


def test_main_summary_deduplicates_and_counts_answered(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["visa_probe.py"])
    monkeypatch.setattr(
        vp,
        "BACKEND_CANDIDATES",
        [
            ("Backend 1", ""),
            ("Backend 2", ""),
        ],
    )

    child_output = (
        "  1 resource(s):\n"
        "    RESOURCE: GPIB0::2::INSTR KEITHLEY INSTRUMENTS INC.,MODEL 6482,4008420,A01\n"
    )
    dummy_proc = subprocess.CompletedProcess(
        args=["python", "visa_probe.py", "--backend", ""],
        returncode=0,
        stdout=child_output,
        stderr="",
    )

    with patch("subprocess.run", return_value=dummy_proc):
        ret = vp.main()

    assert ret == 0
    captured = capsys.readouterr().out
    assert "USING: Backend 1" in captured
    assert "GPIB  1 answered" in captured
    assert "USB   NONE ANSWERED" in captured
