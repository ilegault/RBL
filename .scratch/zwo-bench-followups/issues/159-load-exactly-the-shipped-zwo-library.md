# 159: Load exactly the shipped ZWO library, quietly, and record the real serial and SDK version

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/zwo-bench-followups/spec.md` (D2, D3; user stories 28-32)
**Binding:** `docs/adr/0008-zwo-camera-owned-by-a-capture-process.md` (decision 2: the DLL ships byte for byte and is used by explicit path), `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/capture/camera.py` (`CameraInfo`, `ZwoCamera.open` including its serial and `sdk_version` block), `src/rbl/capture/process.py` (`main`, the `--camera zwo` branch, and `_log`), `tools/zwo_bench.py` (`main`), `tests/test_zwo_camera.py` (`_StandInZwoasiModule`, the `stand_in_zwoasi` fixture, test_open_passes_the_explicit_dll_path, test_info_and_bayer_mapping), `tests/test_zwo_packaging.py` (test_bench_tool_is_standalone: the bench tool must stay free of `rbl` imports and module-level `import zwoasi`).

## Why

zwoasi 0.2.0 does two things on import: it calls `init()` with `ctypes.util.find_library('ASICamera2')` (a search of `PATH`), and when that finds nothing it logs `logging.warning('ASI SDK library not found')` on the root logger. Its `init(path)` returns immediately when the module-level `zwolib` is already set. So:

- every successful Open writes a false WARNING to `rbl.log` (seen on the bench, 2026-11-20);
- on a PC where another `ASICamera2.dll` is on `PATH` (ZWO's ASIStudio installs one), RBL's `init(<vendor path>)` silently does nothing and RBL runs a different ZWO library from the one it ships.

`zwolib` is a `ctypes.CDLL` whose `_name` is the path it was loaded from. zwoasi 0.2.0 does not wrap `ASIGetSerialNumber` or `ASIGetSDKVersion`, so today the serial falls back to the camera index (`0`) and `sdk_version` records the wrapper's version.

## What to build

1. `src/rbl/capture/camera.py`: add `_log = logging.getLogger("rbl.capture")` (the Capture Process's logger name). Add the module-level function `import_quietly(module_name: str) -> types.ModuleType`: `logging.disable(logging.WARNING)`, then `importlib.import_module(module_name)`, with `logging.disable(logging.NOTSET)` in a `finally`.
2. Same file: add `load_zwo_library(sdk_dll: str) -> types.ModuleType`: `zwoasi = import_quietly("zwoasi")`. If `zwoasi.zwolib` is not None and its `_name` is not the same file as `sdk_dll` (compare `os.path.normcase(os.path.abspath(...))` of both), set `zwoasi.zwolib = None`. Then call `zwoasi.init(sdk_dll)`, log `ZWO library loaded: <zwoasi.zwolib._name>` at INFO, and return the module. A handle already loaded from the same file is kept, not reloaded.
3. `ZwoCamera.open`: replace its `import zwoasi` and `zwoasi.init(self._sdk_dll)` with `zwoasi = load_zwo_library(self._sdk_dll)`. The other function-level `import zwoasi` lines in the class stay.
4. Serial, in `ZwoCamera.open`, in this order: (a) `sn = (ctypes.c_ubyte * 8)()`, then `rc = zwoasi.zwolib.ASIGetSerialNumber(self._cam.id, sn)`, passing the array itself; if this raises `AttributeError` or `rc != 0`, there is no serial; otherwise the serial is `bytes(sn).hex().upper()` (16 characters); (b) failing that, the existing `get_id()` value, then (c) the existing `CameraID` fallback. When (a) fails, log WARNING `ZWO serial number unavailable; using <the fallback value>`.
5. SDK version: `fn = zwoasi.zwolib.ASIGetSDKVersion`, then `fn.restype = ctypes.c_char_p`, then the value is `fn().decode("ascii", "replace").strip()`. On `AttributeError` or a `None` result, use `f"unknown (zwoasi {zwoasi.__version__})"`. This replaces the `getattr(zwoasi, "sdk_version", ...)` line.
6. `src/rbl/capture/process.py`, `main`, `--camera zwo` branch: replace `import zwoasi` and `zwoasi.init(args.sdk_dll)` with `zwoasi = load_zwo_library(args.sdk_dll)` (imported from `rbl.capture.camera`). An exception still becomes the `fatal` message, as now.
7. `tools/zwo_bench.py`, `main`: it must not import `rbl`, so repeat steps 1-2 inline: wrap `import zwoasi` in `logging.disable(logging.WARNING)` / `logging.disable(logging.NOTSET)`, drop a `zwolib` loaded from a different file, `init(args.sdk_dll)`, then `print(f"ZWO library loaded: {zwoasi.zwolib._name}")`.
8. `tests/test_zwo_camera.py` stand-in (needed by every criterion): `_StandInZwoasiModule` gains `zwolib = None`. Its `init(path)` behaves like 0.2.0's: it returns immediately when the **installed** module's `zwolib` is not None, and otherwise sets the installed module's `zwolib` to a new `_StandInLib(path)` and records the call as today. The fixture copies attributes onto a `ModuleType`, so bind `init` to that module object (for example, a closure over `mod` set in the fixture). `_StandInLib` has `_name`; `ASIGetSerialNumber(camera_id, arr)`, which writes the bytes 0x01-0x08 into `arr` and returns a settable return code (default 0); and `ASIGetSDKVersion`, a plain function returning `b"1, 41, 0, 0"` that accepts a `restype` attribute.
9. Update `ZwoCamera`'s docstring: explicit loading, why a pre-loaded handle is dropped, and the serial order.

## Acceptance criteria

- [ ] Test `test_a_library_found_on_path_is_replaced_by_the_explicit_one` in `tests/test_zwo_camera.py`: with the installed stand-in's `zwolib` pre-set to a `_StandInLib("C:/other/ASICamera2.dll")`, `ZwoCamera("C:/x/ASICamera2.dll").open(0)` leaves the module's `zwolib._name == "C:/x/ASICamera2.dll"`, and `init` was called with that path. With `zwolib` pre-set to a lib already named `C:/x/ASICamera2.dll`, the module's `zwolib` after open **is** the same object.
- [ ] Test `test_import_quietly_hides_only_the_import_warning` in `tests/test_zwo_camera.py`: write `tmp_path/noisy_import_probe.py`, whose top level runs `import logging; logging.warning("ASI SDK library not found")`. After `monkeypatch.syspath_prepend(tmp_path)` (and `monkeypatch.delitem(sys.modules, "noisy_import_probe", raising=False)`), `import_quietly("noisy_import_probe")` returns the module, and `caplog` (at WARNING) holds no record. A `logging.getLogger("probe").warning("after")` logged next **is** captured.
- [ ] (no test: an in-place rewrite of an existing test, which CI runs) Rewrite the existing info test in `tests/test_zwo_camera.py` (test_info_and_bayer_mapping) in place, under the same name: `info.serial == "0102030405060708"` and `info.sdk_version == "1, 41, 0, 0"`. Its other assertions stay.
- [ ] Test `test_serial_and_sdk_version_fall_back_with_a_warning` in `tests/test_zwo_camera.py`: with the stand-in lib's `ASIGetSerialNumber` returning 16, `info.serial` is the stand-in's `get_id()` value, and `caplog` has a WARNING on `rbl.capture` containing `ZWO serial number unavailable`. With `ASIGetSDKVersion` deleted from the stand-in lib, `info.sdk_version == "unknown (zwoasi 0.2.0)"`.
- [ ] (no test: CI's full suite) No test is deleted; the Capture Process, packaging and bench-tool tests pass, including the bench tool's standalone check.

**What tests may fake:** the zwoasi module and its library handle (the stand-in). Nothing loads a real DLL. `import_quietly` runs a real import from `tmp_path`.

**Out of scope:** upgrading zwoasi; moving existing Dark Frames out of the `0` folder; the bench tool's measurements; `ZWO_ASI_LIB`.

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile --max-worker-restart=0 tests/test_zwo_camera.py tests/test_zwo_packaging.py
```

Run pytest on the touched test files only (implement-ticket-parallel, section 4). CI runs the full suite on the PR.

## Comments

