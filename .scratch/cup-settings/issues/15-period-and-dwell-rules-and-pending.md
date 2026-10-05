# 15: Period and dwell get rules instead of arbitrary limits, and show what is pending

**Blocked by:** 09, 14

**Status:** ready-for-agent

**Read first:** `src/rbl/gui/faraday_cup_tab.py`, the Sampling Cycle panel: the
`self.spn_cycle_period.setRange(10.0, 86400.0)` and
`self.spn_cycle_dwell.setRange(1.0, 60.0)` calls, `_on_cycle_period_changed`,
`_on_cycle_dwell_changed` and `_update_cycle_view`. `docs/adr/0002-...md` amendment
decision A7. `CONTEXT.md`, the glossary entry "Pending change".

**What to build:** The 60-second dwell ceiling and the 86 400-second period ceiling
are replaced by rules that relate the two fields and the settle window to each other,
and the operator can see when a change they typed takes effect. Neither field ever
locks; an edit made while the cycle is armed is queued, displayed, and applied at the
next boundary.

- [ ] The two `setRange` calls are replaced by wide absolute bounds - `0.1` to
      `604800.0` on both spin boxes - with the real rules enforced by
      `validate_settings` from ticket 02 on every committed edit. A test asserts the
      spin box maximums and that entering a dwell of 400 with a period of 300 is
      refused, because dwell plus two move-confirmation timeouts must fit inside the
      period.
- [ ] A refused edit restores the previous value with `sync_value` and shows the
      warning's `reason` in the `FAULT` role, exactly as the Acquisition Settings
      group does. A test asserts the field value is unchanged after a refusal and that
      the reason is visible. Nothing typed is ever discarded without explanation.
- [ ] An accepted edit calls `set_period` or `set_dwell` on the scheduler (which
      queues it, per ticket 09) and calls
      `self.session_writer.write_settings_changed` once with key `cycle_period_s` or
      `cycle_dwell_s` and the old and new values. A test asserts one row per accepted
      edit.
- [ ] While the scheduler reports a pending value, a label in the `MUTED` role shows
      it with the time it applies, for example `Dwell 20.0 s pending, applies at next
      insertion` and `Period 600 s pending, applies at next period boundary`. The
      label is rendered from `pending_period_s` and `pending_dwell_s`, **not** from
      what the operator typed, so it states what the scheduler will do. It clears when
      the scheduler clears the pending value. A test asserts the text appears after an
      edit while armed and is gone after the boundary tick that applies it.
- [ ] A permanent label in the `MUTED` role under the period and dwell row reads
      exactly `Changes apply from the next insertion.` A test asserts the exact string
      is present whether or not the cycle is armed.

**Tests may fake:** the hardware, via `tests/payloads.py`, and the output directory.
The scheduler, its pending state and the written rows must be real.

**Out of scope:** the "Keep previous schedule" checkbox (ticket 16), and locking these
two fields - they are never locked.
