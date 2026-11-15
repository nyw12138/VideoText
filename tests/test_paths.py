from pathlib import Path

from videotext.models import MediaMetadata
from videotext.paths import output_directory, safe_component


def test_safe_component_removes_windows_invalid_characters_and_trailing_dots():
    assert safe_component(' A<B>:"C/D\\E|F?G*... ') == "A_B_C_D_E_F_G"


def test_safe_component_handles_reserved_name():
    assert safe_component("CON") == "_CON"


def test_output_directory_includes_safe_title_and_video_id(tmp_path: Path):
    item = MediaMetadata(
        source_url="https://example.com/1",
        platform="Example",
        video_id="abc/123",
        title="标题:演示",
        duration=20.0,
        is_live=False,
        is_playlist=False,
    )
    assert output_directory(tmp_path, item) == tmp_path / "标题_演示_abc_123"
