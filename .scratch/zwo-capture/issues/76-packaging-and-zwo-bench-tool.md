# 76: Ship the ZWO DLL inside the one-folder build, and `tools/zwo_bench.py`

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 69

**Spec:** `.scratch/zwo-capture/spec.md`, section D12; "Real hardware" → B1
**Binding:** `docs/adr/0008-zwo-camera-owned-by-a-capture-process.md` (decision 2: no new
executable; the DLL ships byte for byte), `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `rbl.spec` (the `all_binaries` block with the ffmpeg warning, and
`all_hiddenimports`); `docs/ANTIVIRUS_FALSE_POSITIVE.md`; `AGENTS.md` §9 trap 5;
`tools/scope_fwhm.py` (module docstring and layout of a standalone bench tool with no `rbl`
imports); `.gitignore`.

## What to build

1. `rbl.spec`: add `"zwoasi"` to `all_hiddenimports` with a comment (`rbl.capture.camera` imports
   it inside a function, which PyInstaller cannot see). Add
   `os.path.join(ROOT, "vendor", "zwo", "ASICamera2.dll")` to `all_binaries` with destination
   `"."` when the file exists; when it does not, `print` a warning: `"*** WARNING: vendor\\zwo\\
   ASICamera2.dll not found — this build cannot open the ZWO camera. See vendor/zwo/README.md
   ***"`. Never an error: a build without the DLL still works for everything else. Keep the
   ffmpeg logic unchanged (combine the lists; do not let the ffmpeg warning fire because the
   DLL is present).
2. `.gitignore`: add `vendor/zwo/*.dll` with a comment (ZWO's DLL is not committed; the
   developer copies it in).
3. `vendor/zwo/README.md` (new): what the file is (ZWO's x64 `ASICamera2.dll` from the ASI
   Camera SDK for Windows, used byte for byte, never renamed or repacked), where it comes from
   (ZWO's developer/SDK download page; the `lib/x64` folder of the Windows SDK archive), where
   to put it (`vendor/zwo/ASICamera2.dll`), that `RBL_ZWO_SDK_DLL` overrides the location in
   development, and a `SHA-256:` line reading `to be recorded by bench ticket 80`.
4. `tools/zwo_bench.py` (new): standalone like `tools/scope_fwhm.py` — **no `rbl` import**,
   module docstring with `WHY THIS EXISTS` and a flow-of-operations block. Arguments:
   `--sdk-dll PATH` (required), `--seconds` (default 10), `--camera-index` (default 0). It opens
   the camera with `zwoasi`, auto exposure and gain off, and for each of full sensor RAW16, full
   sensor RAW8, 640 × 400 RAW16 and 640 × 400 RAW8 streams for `--seconds`, then prints one
   line per case: size, bit depth, frames, measured fps, dropped frames
   (`get_dropped_frames`). Then one RAW16 full frame's distinct-levels check: the number of
   distinct values (`len(np.unique(img))`) and the first 20 distinct values of `img % 16`
   (`np.unique(img % 16)[:20]`). Put the per-case summary and the levels check in two pure
   functions, `fps_summary(frames: int, seconds: float, dropped: int) -> dict` and
   `levels_report(img: np.ndarray) -> dict`, so they can be tested without a camera. `import
   zwoasi` happens inside `main`, so `--help` works on a PC without it.

## Acceptance criteria

`tests/test_zwo_packaging.py` (new).

- [ ] `test_bench_tool_is_standalone`: AST scan of `tools/zwo_bench.py` finds no import of
  `rbl` or any `rbl.*` module and no module-level `import zwoasi`; and
  `subprocess.run([sys.executable, "tools/zwo_bench.py", "--help"], cwd=<repo root>)` exits 0
  with `--sdk-dll` in its output.
- [ ] `test_levels_report_and_fps_summary` (import the tool by path with `importlib`):
  `levels_report` on a `uint16` array of the values `{0, 16, 32, 4080}` repeated reports
  4 distinct levels and `img % 16` levels `[0]` (so 12-bit data in 16-bit pixels shows up as
  low nibble always 0); on `np.arange(64, dtype=np.uint16)` it reports 64 levels and 16 low-
  nibble values; `fps_summary(500, 10.0, 3)` gives `fps == 50.0`, `dropped == 3`.
- [ ] `test_spec_bundles_zwoasi_and_the_dll`: reading `rbl.spec` as text, `"zwoasi"` appears in
  the `all_hiddenimports` list and the path segments `"vendor"`, `"zwo"`, `"ASICamera2.dll"`
  appear in the binaries logic; `.gitignore` contains the line `vendor/zwo/*.dll`; and
  `vendor/zwo/README.md` exists and contains `SHA-256:`.
- [ ] (by hand) Stated in the PR, since it cannot run in CI: `pyinstaller rbl.spec --clean` on the
  Windows dev PC without the DLL prints the warning and builds; with the DLL in
  `vendor/zwo/`, `dist/RBL/` (the one-folder output; check the actual folder name) contains
  `ASICamera2.dll` with the same SHA-256 as the source file.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile

## Out of scope

- Committing the DLL, an installer, a driver, or any second executable.
- Code signing.
- Changes to `build.bat` (it copies `dist/` as a folder, which now carries the DLL).

## Comments
