# RBL.exe flagged by Windows Defender — what we know, and what we need

Prepared 25 Aug 2026 for UW-Madison NEEP / Ion Beam Laboratory IT.

## The detection, confirmed

    Trojan:Win32/Bearfoos.B!ml
    Severity: Severe    Status: Quarantined    25 Aug 2026

    Affected items:
      C:\Users\ilegault\Desktop\RBL3.86\RBL.exe
      C:\Users\ilegault\Desktop\RBL\RBL.exe

Three things about this verdict matter:

1. **The `!ml` suffix means machine learning.** It is a model score against
   the file's structure, not a signature match against a known malicious
   sample. Microsoft uses this suffix specifically to distinguish heuristic
   verdicts from confirmed-family detections.
2. **Microsoft's own page for it lists "Aliases: No associated aliases".** A
   genuine malware family carries cross-vendor names (Kaspersky, ESET,
   Symantec equivalents). A generic ML bucket does not, because no other
   vendor has a corresponding thing to name. The page's description — "can
   perform a number of actions of a malicious actor's choosing" — is
   Microsoft's boilerplate generic-trojan text, not an analysis of this file.
3. **`RBL.exe` itself was flagged, not any bundled library.** All 59 DLLs in
   `_internal\` passed. That points squarely at the PyInstaller bootloader
   stub, which is the part of `RBL.exe` that is not our code.

### Microsoft's own software hit this exact detection

`Trojan:Win32/Bearfoos.B!ml` fired on a binary in **Microsoft's own `apm`
repository** (github.com/microsoft/apm, issue #487). It was confirmed there
as a false positive on a PyInstaller-built executable, and the remediation
the Microsoft team applied was:

| Their fix | Our status |
|---|---|
| Embed Windows PE version metadata | **Done** — `version_info.txt`, wired into the build |
| Disable UPX compression on Windows | **Already off** — `upx=False` in `rbl.spec` |
| Document the issue for users | **This document** |
| Submit the revised binary to Microsoft for FP review | **Next step** |
| Longer term: Authenticode code signing | **Requesting** — see below |

We arrived at that list independently before finding their issue. It is the
same list because it is the same problem.

## The ask, up front

`RBL.exe` is in-house scientific software written and built by this lab. We
believe the Defender block is a **generic false positive on the PyInstaller
runtime**, not a detection of anything the program does. We are asking for
either:

1. the **detection name** from Protection History, so we can confirm that and
   submit it to Microsoft as a false positive; and
2. an **allowlist entry** for the deploy folder while that submission is
   processed.

We are *not* asking anyone to disable Defender or reduce scanning anywhere.

## What the software is

A desktop control and analysis application for the Right Beam Line in the Ion
Beam Laboratory. It plans raster patterns, drives the electrostatic steerer
through two function generators, reads vacuum gauges, slit motors and an
oscilloscope, and records diagnostics. Roughly 100 Python source files, built
into a Windows executable with **PyInstaller** (a standard, widely used
Python-to-.exe packager) on top of PySide6/Qt, NumPy, SciPy, matplotlib and
OpenCV.

Full source is on hand and can be read by IT on request. Nothing is
obfuscated, minified or pre-compiled.

## Why an unsigned PyInstaller build gets flagged

PyInstaller executables all share the same bootloader stub: the .exe unpacks a
compressed Python runtime to a temp directory and executes it. That is
structurally similar to what a self-extracting dropper does, so machine-
learning detections fire on it regularly. The usual verdict names are
`Trojan:Win32/Wacatac.B!ml`, `Program:Win32/Wacapew.C!ml` or
`Trojan:Win32/Presenoker` — the `!ml` suffix marks a machine-learning score,
not a signature match against known malware.

Two things make it worse for us specifically:

- **The binary is unsigned.** We hold no code-signing certificate.
- **Every rebuild produces a new hash** no reputation system has seen before.
  A build compiled an hour ago has zero prevalence, which ML scoring treats as
  a risk factor in its own right.

## Evidence the program is benign

The full source tree was scanned for the behaviours these detections are
supposed to represent. Results:

| Looked for | Found |
|---|---|
| Network egress (`urllib`, `requests`, `socket`, any URL) | **None anywhere in the source** |
| Dynamic code execution (`eval`, `exec` of data, `marshal`/`pickle.loads`) | **None** (the `.exec()` hits are Qt's `QApplication.exec()` / `QDialog.exec()`) |
| Obfuscation, base64 blobs, packed payloads | **None**; `upx=False` in the build spec |
| Embedded executables inside the bundle | **None as of this build** — see below |
| Persistence, registry run keys, scheduled tasks, service install | **None** |
| Credential, browser or filesystem harvesting | **None** |

The program writes only to its own log/data folders and to
`%USERPROFILE%\.config\rbl\`. Its outbound I/O is entirely local
instrumentation: a TCP socket to the Galil motion controller at a fixed lab
address, USB/VISA to the function generators, and RS-232 to the gauges and
scope.

Two constructs in the source do look unusual out of context, and we would
rather name them than have them found:

- `rbl/driver.py` calls `ShellExecuteW(..., "runas", ...)` to launch a
  **vendor driver installer with an elevation prompt**. It installs nothing
  itself; it hands off to LabJack's or NI's own signed installer, and the user
  sees the standard UAC dialog. **The installers are no longer shipped with
  the app at all** (see below), so this path is now inert unless someone puts
  an installer next to the .exe deliberately.
- `rbl/services/video_transcoder.py` passes `CREATE_NO_WINDOW` when spawning
  `ffmpeg`, to stop a console window flashing over the GUI. **ffmpeg is
  deliberately not bundled** and this code path is disabled in lab builds; the
  app keeps `.avi` and says so in its own UI.

## What we already changed, and what it did not fix

1. **Removed the bundled third-party installers.** `vendor\` previously held
   `LabJackBasic_*.exe` and `ni-visa_*_online.exe`, and the build embedded
   them *inside* the application archive. An unsigned .exe carrying other
   .exe files — one of them a network downloader stub — is a genuine dropper
   heuristic, so they were taken out of the bundle entirely. **The block
   persisted after this change**, which is consistent with the bootloader
   being what is scored, not the payload.
2. **Added a truthful version resource.** The binary previously had entirely
   blank file properties. It now declares company, product, description and
   version (3.86.0.0), so a block event can be traced to a specific build.
3. **Confirmed no compression/packing.** `upx=False`, one-folder layout.

We have deliberately **not** attempted any of the workarounds that circulate
for this problem — recompiling the PyInstaller bootloader, repacking, or
otherwise altering the binary to change what a scanner matches on. Those are
evasion techniques, they are what actual malware does, and using them would
make this harder to trust rather than easier.

## What we need

**From IT:**

- An **allow indicator on the SHA256** of the current build, or a path
  exclusion for the deploy folder, so the app can run while the submission is
  processed. Per Microsoft's own guidance this is the intended interim step
  for a confirmed false positive, not a workaround.
- If the lab is under Defender for Endpoint, the alert can also be classified
  as a false positive in the security portal and the quarantine action undone
  from Action center → History → Undo.

**One verification we suggest before any of that**, because it costs nothing
and rules out the alternative explanation: have someone build the app from a
clean checkout **on a different machine** and scan the result. If a
freshly-built binary from a different host is flagged identically, the
detection is a property of the PyInstaller bootloader and not of our build
machine or our source tree. If it comes back clean, we want to know that
urgently — it would mean this build host needs looking at, and we would stop
deploying immediately.

**From us:**

- We will submit the sample to Microsoft at
  <https://www.microsoft.com/en-us/wdsi/filesubmission> as a suspected false
  positive, citing the `apm` precedent above. Submissions from an account
  holding the institution's Software Assurance ID are prioritised, so if the
  university has a SAID we would rather submit under it than as an
  individual.
- We are pursuing a **code-signing certificate**, which is the only durable
  fix. Signing lets IT allowlist by *publisher*, which survives every rebuild;
  a hash allowlist expires the next time we compile.

## Reproduction / verification

The lab can rebuild from source at any time:

```
cd C:\Users\IGLeg\PycharmProjects\RBL
.venv\Scripts\activate
pyinstaller rbl.spec --clean
```

Output is `dist\RBL\` — a one-folder distribution, `RBL.exe` plus an
`_internal\` directory of Qt/NumPy/SciPy libraries. IT is welcome to build it
themselves on a machine they control and compare hashes.
