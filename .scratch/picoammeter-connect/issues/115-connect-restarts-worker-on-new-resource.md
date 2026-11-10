# 115: Connect with a new address restarts the picoammeter worker

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/faraday-cup-reader/spec.md` (the reader; this ticket fixes its connect lifecycle)
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`

## What to build

On the lab PC on 2026-11-10 the Faraday Cup tab's resource box read `GPIB0::02::INSTR`,
but the log showed the picoammeter worker retrying `GPIB0::14::INSTR` every 30 s, and each
press of **Connect** logged `picoammeter_link: worker already running` and did nothing.

Cause: `connect_picoammeter()` in `src/rbl/state/picoammeter_link.py` returns early whenever
`self._picoammeter_worker` is not `None`. A worker whose connect failed keeps running (it
retries on the `CUP_RECONNECT_BACKOFF_S` ladder in `PicoammeterWorker.run`), so the first
address ever requested is the only one the app will try until it is restarted. Meanwhile
the tab shows "Disconnected" and a **Connect** button, so the operator has no way out.

After this ticket the rule is: **the worker always runs on the most recently requested
VISA resource string.** A connect request with a resource string different from the one
the running worker was started with (compare `resource.strip()` to
`self._picoammeter_resource` exactly, no other normalisation) stops that worker, waits for
it to exit, and starts a new one. A request with the same resource string keeps today's
no-op (the worker is already retrying it on its own ladder).

The wait is a requirement, not a nicety: two workers opening the same 6482 at once (for
example `GPIB0::02::INSTR` and `GPIB0::2::INSTR` are the same instrument) would collide on
the GPIB bus. The wait bound is one second longer than the driver's 5000 ms VISA timeout
in `Keithley6482.__init__`, so a worker blocked inside `viOpen` can still finish.

## Acceptance criteria

- [ ] `src/rbl/config/cup_config.py` gains `CUP_WORKER_RESTART_WAIT_MS: int = 6000`
      directly under `CUP_RECONNECT_BACKOFF_S`, with a one-line comment: driver VISA
      timeout (5000 ms) plus one second. In `connect_picoammeter()`, when a worker exists
      and the stripped requested resource differs from `self._picoammeter_resource`: keep
      a reference to the old worker, call `self.disconnect_picoammeter()` (it detaches the
      signals and calls `stop()`), then call `old.wait(CUP_WORKER_RESTART_WAIT_MS)`, then
      start the new worker exactly as the existing code does. Test:
      tests/test_picoammeter_link.py::`test_connect_with_new_resource_restarts_worker` —
      patch `rbl.state.picoammeter_link.PicoammeterWorker` with `side_effect=[w1, w2]`
      (two `MagicMock`s, `w1.wait.return_value = True`); `connect_picoammeter("GPIB0::14::INSTR")`
      then `connect_picoammeter("GPIB0::2::INSTR")`; assert `w1.stop` called once,
      `w1.wait` called with `CUP_WORKER_RESTART_WAIT_MS`, the class's `call_args_list`
      equals `[call(resource_name="GPIB0::14::INSTR"), call(resource_name="GPIB0::2::INSTR")]`,
      and `w2.start` called once.
- [ ] If the old worker does not exit within the wait, no new worker is started and the
      operator is told. `connect_picoammeter()` emits `self.cup_error` with exactly
      `"Previous picoammeter connection did not stop; press Connect again."` and returns.
      Test: tests/test_picoammeter_link.py::`test_restart_refuses_when_old_worker_will_not_stop`
      — same patch, `w1.wait.return_value = False`; collect `beamline.cup_error`
      emissions; after the second connect the class was constructed once, `w2.start` was
      never called, and the collected list contains that exact string.
- [ ] The same resource string stays a no-op. The existing test
      tests/test_picoammeter_link.py::`test_connect_and_disconnect_picoammeter` is not
      edited and still passes (its second call with the same resource asserts
      `start.call_count == 1`).
- [ ] The operator path works end to end through the tab. Test:
      tests/test_faraday_cup_tab.py::`test_connect_button_retargets_a_retrying_worker` —
      real `Beamline` and `FaradayCupTab`; patch only
      `rbl.state.picoammeter_link.PicoammeterWorker` (two `MagicMock` workers, first
      `wait` returns `True`); set `tab.le_resource` to `"GPIB0::14::INSTR"`, call
      `tab.btn_connect.click()`; set it to `"GPIB0::2::INSTR"`, click again; the patched
      class's last call is `call(resource_name="GPIB0::2::INSTR")`. No reading is ingested
      between the clicks, so the tab is in its disconnected view the whole time, as on the
      lab PC.
- [ ] The `connect_picoammeter()` docstring and the module docstring of
      `src/rbl/state/picoammeter_link.py` gain a short **RETARGETING** paragraph stating
      the rule above, the lab PC incident, and why the old worker is waited for. (by hand)

**What tests may fake:** `PicoammeterWorker` (patched at `rbl.state.picoammeter_link`).
`Beamline`, `PicoammeterLinkMixin` and `FaradayCupTab` are real and unpatched.

**Out of scope:** the error text shown in the tab after a failed connect (ticket 116).
Hints for VISA errors (ticket 117). Changing the backoff ladder. Any "Retrying…" status
display. Auto-discovery in `PicoammeterWorker._find_resource`.

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments
