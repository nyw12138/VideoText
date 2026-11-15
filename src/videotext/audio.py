import subprocess
from collections.abc import Callable
from pathlib import Path

from .errors import EnvironmentError, TranscriptionError

Runner = Callable[..., subprocess.CompletedProcess[str]]


class FfmpegAudioNormalizer:
    def __init__(self, runner: Runner = subprocess.run):
        self.runner = runner

    def check_available(self) -> None:
        try:
            result = self.runner(
                ["ffmpeg", "-version"], capture_output=True, text=True, shell=False
            )
        except FileNotFoundError as exc:
            raise EnvironmentError(
                "未找到 FFmpeg。", "请安装 FFmpeg 并将其加入 PATH。"
            ) from exc
        if result.returncode != 0:
            raise EnvironmentError("FFmpeg 无法正常运行。")

    def normalize(self, source: Path, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(destination),
        ]
        try:
            result = self.runner(command, capture_output=True, text=True, shell=False)
        except FileNotFoundError as exc:
            raise EnvironmentError(
                "未找到 FFmpeg。", "请安装 FFmpeg 并将其加入 PATH。"
            ) from exc
        if result.returncode != 0 or not destination.is_file():
            detail = result.stderr.strip() or "未生成标准化音频"
            raise TranscriptionError(f"音频标准化失败：{detail}")
        return destination
