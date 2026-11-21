import json
import math
import subprocess
from collections.abc import Callable
from pathlib import Path

from .errors import EnvironmentError, InputError, TranscriptionError

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

    def probe_local_media(self, source: Path) -> float:
        try:
            result = self.runner(
                [
                    "ffprobe", "-v", "error", "-show_entries",
                    "format=duration:stream=codec_type,duration", "-of", "json", str(source),
                ],
                capture_output=True,
                text=True,
                shell=False,
            )
        except FileNotFoundError as exc:
            raise EnvironmentError(
                "未找到 FFprobe。", "请安装包含 ffprobe 的 FFmpeg 并将其加入 PATH。"
            ) from exc
        if result.returncode != 0:
            detail = result.stderr.strip() or "无法读取媒体信息"
            raise InputError(f"无法解析所选媒体文件：{detail}", "请确认文件未损坏且格式受支持。")
        try:
            data = json.loads(result.stdout)
            streams = data["streams"]
            has_audio = any(stream.get("codec_type") == "audio" for stream in streams)
            raw_duration = data.get("format", {}).get("duration")
            if raw_duration is None:
                durations = [
                    float(stream["duration"])
                    for stream in streams
                    if stream.get("duration") is not None
                ]
                raw_duration = max(durations)
            duration = float(raw_duration)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise InputError("无法读取媒体时长或音轨信息。", "请选择包含音轨的有效媒体文件。") from exc
        if not has_audio:
            raise InputError("所选文件不包含音轨。", "请选择带有声音的音频或视频文件。")
        if not math.isfinite(duration) or duration <= 0:
            raise InputError("媒体时长无效。", "请选择时长信息有效的媒体文件。")
        return duration

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
