"""
Unit tests for rbl.config.persistence — the on-disk JSON config store used to
persist function-generator settings across a USB replug.
"""
import rbl.config.persistence as persistence


def test_load_config_missing_file_returns_empty_dict(tmp_path, monkeypatch):
    monkeypatch.setattr(persistence, "CONFIG_PATH", tmp_path / "does_not_exist.json")
    assert persistence.load_config() == {}


def test_save_then_load_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(persistence, "CONFIG_PATH", tmp_path / "sub" / "funcgen.json")
    data = {"A1": {"freq": 1000.0, "amp": 2.0}}
    persistence.save_config(data)
    assert persistence.load_config() == data


def test_save_creates_parent_directory(tmp_path, monkeypatch):
    path = tmp_path / "nested" / "dir" / "funcgen.json"
    monkeypatch.setattr(persistence, "CONFIG_PATH", path)
    persistence.save_config({"x": 1})
    assert path.exists()


def test_load_config_corrupt_file_returns_empty_dict(tmp_path, monkeypatch):
    path = tmp_path / "funcgen.json"
    path.write_text("not valid json{{{")
    monkeypatch.setattr(persistence, "CONFIG_PATH", path)
    assert persistence.load_config() == {}
