# 06: Expected-failure markers become strict

**What to build:** A muted test that starts passing becomes a build failure
instead of silence.

The strict setting for expected-failure markers is turned on repository-wide in
the pytest configuration, and no marker remains anywhere in the suite. From this
point, marking a test expected-to-fail in order to green a build is itself a
build failure. Expected-failure is not a tool for greening a build.

**Blocked by:** 05

**Status:** ready-for-agent

- [ ] The pytest configuration sets strict expected-failure behaviour repository-wide.
- [ ] A search of the test directory finds zero expected-failure markers.
- [ ] Adding a marker to a passing test fails the build — demonstrated once in a CI run, then reverted.
- [ ] The full suite is green in CI.

Reference: ADR 0001 decision 2.
