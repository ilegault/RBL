# 08: Session record

**What to build:** Every sample inside an acquisition run is written to a session file,
so an irradiation can be reconstructed afterwards from the application's own records
rather than from what somebody wrote down.

One file per application session, not one per insertion. Rows are the samples inside
runs. Idle periods are not written as sample rows — but the record must still show that
the application was watching, so that a gap between runs is never ambiguous between
three different causes: the cup was out and we were polling; the application was not
running; the instrument was disconnected.

**Blocked by:** 07

**Status:** ready-for-agent

- [ ] One file per session, in the application's existing data output location, under
      its own subdirectory
- [ ] Sample rows carry host timestamp, instrument timestamp, current in amps, status
      word, over-range flag, and run identifier
- [ ] Run identifiers increment per insertion and let an analyst separate insertions
- [ ] A run-opened marker records the thresholds in force when the run began
- [ ] A run-closed marker is written when a run ends, including when forced
- [ ] A periodic idle heartbeat is written while no run is active
- [ ] Disconnection and reconnection are recorded as markers
- [ ] The file is flushed after every row — a run that dies at hour eleven leaves eleven
      hours of usable data
- [ ] Over-range samples are written and flagged, never silently dropped
- [ ] A test drives a full insertion through the cup feed helper and asserts on the file
      contents, including markers
- [ ] A test asserts that an idle gap and a disconnected gap are distinguishable in the file
