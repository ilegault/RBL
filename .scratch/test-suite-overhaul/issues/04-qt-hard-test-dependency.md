# 04: Qt is a hard test dependency

**What to build:** Qt is required to run the suite, and the suite says so
uniformly.

Every import-skip guard on the Qt bindings is removed from the test modules that
carry one — roughly twenty — which leaves them consistent with the ten or so
Qt-dependent modules that already import unguarded. Qt is named in the
development requirements so a fresh environment installs it. A run in an
environment without Qt then fails at import, loudly, rather than yielding a
fraction of the suite or aborting collection for the whole run depending on
which module the collector reached first. A missing dependency is an environment
failure and should be loud.

Note: the spec's user story 9 asks for the opposite — a degraded subset rather
than zero tests. The Implementation Decisions section supersedes it, and this
ticket follows the decision.

**Blocked by:** 01

**Status:** ready-for-agent

- [ ] No test module contains an import-skip guard on the Qt bindings.
- [ ] Qt is listed in the development requirements file.
- [ ] The full suite collects the same number of tests in CI as the ticket 01 baseline.
- [ ] A run with Qt uninstalled fails with an import error rather than a zero-test or partial collection.

Reference: spec section "Qt as a hard test dependency".
