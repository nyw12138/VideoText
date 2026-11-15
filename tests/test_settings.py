import json
from pathlib import Path

from videotext.settings import GuiSettings, default_settings, load_settings, save_settings


def test_defaults_point_to_project_outputs(tmp_path: Path):
    settings = default_settings(tmp_path)
    assert settings.output_root == tmp_path / "outputs"
    assert settings.model == "large-v3-turbo"
    assert settings.language is None
    assert settings.open_output is True


def test_settings_round_trip_and_excludes_sensitive_values(tmp_path: Path):
    path = tmp_path / "settings.json"
    settings = GuiSettings("medium", "zh", tmp_path / "out", True, True, False, "900x650")
    save_settings(path, settings)
    assert load_settings(path, tmp_path) == settings
    text = path.read_text(encoding="utf-8").lower()
    assert "url" not in text
    assert "cookie" not in text
    assert "token" not in text


def test_corrupt_settings_fall_back_to_defaults(tmp_path: Path):
    path = tmp_path / "settings.json"
    path.write_text("{bad", encoding="utf-8")
    assert load_settings(path, tmp_path) == default_settings(tmp_path)


def test_unknown_model_falls_back_to_default(tmp_path: Path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"model": "unknown"}), encoding="utf-8")
    assert load_settings(path, tmp_path).model == "large-v3-turbo"
