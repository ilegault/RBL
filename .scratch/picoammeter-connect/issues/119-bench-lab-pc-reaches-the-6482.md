# 119: Bench — the lab PC reaches the 6482 over GPIB

**Status:** ready-for-developer

**Runner:** developer

**Auto-merge:** no

**Blocked by:** None (can start immediately; the last criterion needs 115, 116 and 117 done)

**Spec:** `.scratch/faraday-cup-reader/spec.md`

**An agent must not claim this ticket.**

## What this establishes

On 2026-11-10 the same 6482, GPIB cable and 82357B answered `*IDN?` from the developer's
laptop (`GPIB0::2::INSTR`, `KEITHLEY INSTRUMENTS INC.,MODEL 6482,4008420,A01`, through the
default NI `visa32.dll` with no extra DLL paths). On the lab PC the RBL build failed every
attempt with `VI_ERROR_LIBRARY_NFOUND`. So the instrument chain is good and the lab PC's
VISA setup is not. `LIBRARY_NFOUND` means VISA loaded but a library it needs for the GPIB
adapter did not; the likeliest cause is Keysight IO Libraries Suite missing or incomplete
on that PC, since NI-VISA reaches the 82357B only through it.

Background: `.scratch/faraday-cup-reader/issues/01-keysight-visa-bench-verification.md`.

## Checklist

- [ ] The 82357B is plugged into the lab PC (it can only be on one machine). Keysight
      Connection Expert is installed there and shows the adapter; after a rescan the 6482
      is at `GPIB0::2::INSTR`. Record the IO Libraries version in Comments. (by hand)
- [ ] `*IDN?` sent from Connection Expert returns the 6482's identification string.
      Connection Expert is then closed. (by hand)
- [ ] RBL on the lab PC connects on `GPIB0::2::INSTR`. If it still reports
      `VI_ERROR_LIBRARY_NFOUND` while Connection Expert can talk to the 6482, create
      `C:\Users\<lab-user>\.pyvisarc` with `[Paths]`,
      `VISA library: C:\Program Files\IVI Foundation\VISA\Win64\ktvisa\ktbin\ktvisa32.dll`
      and `dll_extra_paths: C:\Program Files\IVI Foundation\VISA\Win64\ktvisa\ktbin`,
      restart RBL, and record in Comments that this machine setting is in use. Do not pin
      a VISA path in application code. (by hand)
- [ ] After 115, 116 and 117 are done: rebuild with `build.bat` from current `master`,
      deploy to the lab PC, and confirm the Faraday Cup tab connects and shows a cup
      current. Record the commit hash deployed. (by hand)
- [ ] Findings recorded under `## Comments`, including what fixed it. (by hand)

## Comments
