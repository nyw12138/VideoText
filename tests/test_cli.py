from pathlib import Path
import tomllib

from typer.testing import CliRunner

from videotext.cli import app
from videotext.errors import InputError
from videotext.service import ProgressEvent

runner = CliRunner()


class SuccessfulService:
    def run(self, options, on_progress):
        on_progress(ProgressEvent("done", "处理完成"))
        return (options.output_root / "Demo_1" / "transcript.txt",)


class FailingService:
    def run(self, options, on_progress):
        raise InputError("第一版不支持播放列表。", "请提供单个视频链接。")


def test_cli_success_prints_result_and_uses_defaults(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("videotext.cli._build_service", lambda: SuccessfulService())
    result = runner.invoke(app, ["https://example.com/1", "--output", str(tmp_path)])
    assert result.exit_code == 0
    assert "处理完成" in result.stdout
    assert "transcript.txt" in result.stdout


def test_cli_domain_error_prints_hint_and_uses_typed_exit_code(monkeypatch):
    monkeypatch.setattr("videotext.cli._build_service", lambda: FailingService())
    result = runner.invoke(app, ["https://example.com/list"])
    assert result.exit_code == 2
    assert "播放列表" in result.stdout
    assert "单个视频链接" in result.stdout
    assert "Traceback" not in result.stdout


def test_cli_verbose_exposes_unexpected_exception(monkeypatch):
    class Broken:
        def run(self, options, on_progress):
            raise RuntimeError("unexpected")

    monkeypatch.setattr("videotext.cli._build_service", lambda: Broken())
    result = runner.invoke(app, ["https://example.com/1", "--verbose"])
    assert result.exit_code == 1
    assert "RuntimeError" in result.stdout


def test_cli_help_lists_public_options():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for option in (
        "--output",
        "--model",
        "--language",
        "--keep-audio",
        "--overwrite",
        "--verbose",
    ):
        assert option in result.stdout


def test_project_declares_gui_and_worker_entry_points():
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    scripts = project["project"]["scripts"]
    assert scripts["videotext-gui"] == "videotext.gui:main"
    assert scripts["videotext-worker"] == "videotext.worker:main"
