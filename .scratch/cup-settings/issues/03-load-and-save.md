# 03: Reading and writing the settings file, without silent failure

**Blocked by:** 02

**Status:** ready-for-agent

**Read first:** `.scratch/cup-settings/spec.md`, the settings store section.
`docs/adr/0002-...md` amendment decisions A3, A4 and A5.

**What to build:** The two file operations. Reading returns the settings plus whatever
was wrong with the file. Writing is atomic, so a crash mid-write cannot leave a
half-written file on the control PC during an overnight run.

**Deliberately not copied:** `rbl/config/persistence.py` swallows every exception and
returns `{}`. That is the behaviour this ticket must not reproduce. Read it to see
the shape of a small JSON store, then report failures instead of hiding them.

- [ ] `load_settings() -> tuple[CupSettings, list[SettingsLoadWarning]]` reads
      `CUP_SETTINGS_STORE`, passes the parsed dict through `validate_settings`, and
      returns its result. A test writing a file with five valid keys and one invalid
      one asserts the five load and the sixth is reported.
- [ ] A missing file returns `CupSettings.defaults()` and an empty warning list. A
      file that is not valid JSON returns `CupSettings.defaults()` and exactly one
      warning whose `key` is the string `"_file"` and whose `reason` names the path. A
      test asserts both cases. `load_settings` never raises.
- [ ] `save_settings(settings: CupSettings) -> bool` writes a temporary file in the
      same directory as `CUP_SETTINGS_STORE` and moves it into place with
      `os.replace`, returns `True` on success, returns `False` and logs via
      `logging.getLogger(__name__)` on any failure, and never raises. A test asserts a
      successful save leaves exactly one file in the directory (no leftover temporary
      file), and a test pointing the store path at an unwritable location asserts the
      return value is `False`.
- [ ] The written JSON contains the seven keys plus a `_README` key whose value is
      exactly:
      `RBL rewrites this file whenever a setting changes on the Faraday Cup tab. Edit it only while RBL is closed.`
      A test asserts that exact string is present after a save, and that a load of the
      saved file produces zero warnings and a `CupSettings` equal to the one saved.
- [ ] Every test in this ticket points the store path at a `tmp_path` fixture and
      **never touches the operator's real `~/.config/rbl/`**. `tests/conftest.py`
      already has an autouse fixture redirecting the load-calibration store for
      exactly this reason - a test once wrote a fabricated value into the operator's
      real store. Follow that pattern; add an equivalent redirect for this store.

**Tests may fake:** the store path, via `tmp_path` and monkeypatch. The JSON encoding,
the atomic replace and the validation must all be real.

**Out of scope:** anything that reads these settings (tickets 12 and 13), merging a
hand edit made while the app is running (the application owns the file; it does not
merge), and any GUI.
