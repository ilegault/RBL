# 16: End-to-end session test through the main window

**What to build:** One test that builds the whole main window, drives a
realistic run through it, and asserts on what an operator would see.

It constructs the window with no hardware attached, pushes stream windows,
vacuum readings and scope data through the real beamline the way the application
does, and then reads rendered text off the screens — not internal state. A tab
wired to nothing must fail this test rather than show an empty screen and pass
its own unit tests.

Its justification is operational, not stylistic: this application controls
beamline hardware in a laboratory where an operator reads a number off a screen
and turns a knob. One test that exercises the whole wiring path is the cheapest
insurance that the wiring exists.

Written test-first: observed failing — by disconnecting one tab's wiring — before
it is made to pass.

**Blocked by:** 12, 14

**Status:** ready-for-agent

- [ ] One test builds the real main window and drives a run through the real beamline.
- [ ] Its assertions read rendered text an operator would see, not internal widget state.
- [ ] Disconnecting a single tab's wiring makes it fail, demonstrated once and reverted.
- [ ] It runs with no hardware attached.
- [ ] The suite is green in CI.

Reference: spec sections "Solution" and "Modules under test"; ADR 0001 decision 1.
