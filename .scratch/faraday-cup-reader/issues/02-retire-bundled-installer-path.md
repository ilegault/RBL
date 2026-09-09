# 02: Retire the bundled-installer path

**What to build:** The application stops claiming it ships driver installers, because
it does not. The folder that held them is empty and the practice was stopped after the
antivirus incident, but the preflight module, the build script and the build
specification all still describe it as current.

An operator opening the driver preflight should see, for a missing VISA backend, a
message naming the library they actually need to install — Keysight IO Libraries Suite
— rather than an offer to run a bundled installer that is not there.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

Scope is deliberately narrow. Remove the dead bundled-installer feature as one coherent
unit; leave the capability checks alone. They work and they are still used.

- [ ] The installer-lookup helpers, candidate-directory search, and installer launcher
      are removed from the driver preflight module
- [ ] The installer field is removed from the preflight summary, and every consumer of
      that summary is updated
- [ ] The three capability checks (LabJack, VISA, USB-serial) remain and still report
      correctly
- [ ] The VISA capability check's guidance text names Keysight IO Libraries Suite
- [ ] The build script no longer copies installers into the distribution directory, and
      no longer warns when they are absent
- [ ] The build specification's commentary about shipping installers beside the
      executable is replaced with a short note pointing at the antivirus incident document
- [ ] A build still produces a working application
- [ ] No other behaviour in the preflight module changes
