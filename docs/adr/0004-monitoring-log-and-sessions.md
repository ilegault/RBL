# ADR 0004 — The vacuum monitoring log rolls daily; everything else belongs to a session

- **Status:** Accepted
- **Date:** 2026-10-05

## Context

The vacuum logger starts on its own at the first gauge reading after launch and writes
one 1 Hz row to a single CSV until the application closes. Left running for two weeks it
produced a 176 MB file that could not be opened. Nothing in the application ever closed
that file on a schedule.

At the same time the word "session" meant two unrelated things. The session recorder
used it for one Record-to-Stop folder on the Overview tab. The Faraday cup writer used it
for one run of the application, and opened its file at launch whether or not anyone was
irradiating anything. Video was tied to a session by a checkbox that could not change
once the session had started.

## Decision

1. **There is exactly one continuous log: the vacuum monitoring log.** It starts when the
   application opens, as it does now. It is the only log that runs without an operator
   asking for it, and the only log that rolls over on its own.

2. **The monitoring log rolls over at local midnight** (America/Chicago, the control PC's
   clock), not UTC. A new file starts with the first reading after midnight. Files live in
   one folder per month: `data/vacuum/YYYY-MM/vacuum_YYYYMMDDTHHMMSS.csv`, each with its
   own `#` header and JSON sidecar. No row is dropped or duplicated at the boundary.

3. **Stop Logging stops the monitoring log until the operator starts it again** or the
   application is relaunched. Midnight does not restart a stopped log.

4. **A session is one Start Session to Stop Session on the Overview tab, and nothing
   else.** It is one folder. Everything recorded for an experiment is written inside it:
   the periodic beamline CSV, the event log, a 1 Hz `vacuum.csv` in the monitoring-log
   format, the session's cup log, and any video.

5. **Sessions never roll over.** A session is one experiment, and its files share one
   timebase. A session that runs over a weekend is one folder.

6. **The monitoring log and sessions are independent.** Starting or stopping a session
   does not touch the monitoring log, and the session's `vacuum.csv` is written whether
   the monitoring log is running or stopped.

7. **Video is optional within a session, any number of times.** Start Video and Stop Video
   work at any point while a session runs. Every video run continues the session's
   segment numbering and appends to its frame index; no video file is ever overwritten.

8. **One pure helper decides file boundaries and names.** It takes the time as an argument
   and never reads a clock, so midnight, month-end and New Year are tested without
   waiting for them. No file is ever overwritten: a name that already exists gets a
   `_2`, `_3`, ... suffix.

## Consequences

The monitoring log is now a day-indexed archive: the pressure on a given night is one
file, findable by date. A day at 1 Hz is about 86 400 rows, roughly 12 MB.

A session's pressure is recorded twice: in the monitoring log and in the session's own
`vacuum.csv`. That duplication is deliberate. The monitoring log answers "what was the
vacuum doing on this date"; the session file answers "what was the vacuum doing during
this experiment", and it must not depend on whether someone stopped the monitoring log.

The other append-only records (`trip_history.jsonl`, `conditioning_history.jsonl`,
`dynamic_adjustment_history.jsonl`) are not logs in this sense. They hold one small line
per event, and their value is being a single record across months. They do not roll.
