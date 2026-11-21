import hashlib
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .errors import CancelledError, InputError
from .models import MediaMetadata, MediaSource, Transcript, TranscriptionConfig
from .paths import output_directory
from .validation import validate_local_file, validate_media, validate_url


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    stage: str
    message: str


@dataclass(frozen=True, slots=True)
class RunOptions:
    url: str
    output_root: Path
    config: TranscriptionConfig
    keep_audio: bool
    overwrite: bool
    input_type: str = "url"
    file_path: Path | None = None


class Downloader(Protocol):
    def probe(self, url: str) -> MediaMetadata: ...

    def download(self, url: str, target_dir: Path) -> MediaSource: ...


class AudioNormalizer(Protocol):
    def check_available(self) -> None: ...

    def normalize(self, source: Path, destination: Path) -> Path: ...

    def probe_local_media(self, source: Path) -> float: ...


class Transcriber(Protocol):
    def transcribe(
        self,
        audio_path: Path,
        metadata: MediaMetadata,
        config: TranscriptionConfig,
    ) -> Transcript: ...


Exporter = Callable[
    [Transcript, MediaSource, Path, bool, str, Path | None], tuple[Path, ...]
]
ProgressCallback = Callable[[ProgressEvent], None]
CancelCheck = Callable[[], bool]


def _never_cancelled() -> bool:
    return False


class VideoTextService:
    def __init__(
        self,
        downloader: Downloader,
        audio: AudioNormalizer,
        transcriber: Transcriber,
        exporter: Exporter,
        temp_root: Path,
        tool_version: str,
    ):
        self.downloader = downloader
        self.audio = audio
        self.transcriber = transcriber
        self.exporter = exporter
        self.temp_root = temp_root
        self.tool_version = tool_version

    def run(
        self,
        options: RunOptions,
        on_progress: ProgressCallback,
        is_cancelled: CancelCheck = _never_cancelled,
    ) -> tuple[Path, ...]:
        def check_cancelled() -> None:
            if is_cancelled():
                raise CancelledError()

        local_path: Path | None = None
        if options.input_type == "file":
            if options.file_path is None:
                raise InputError("缺少本地文件路径。")
            local_path = validate_local_file(options.file_path)
            on_progress(ProgressEvent("probe", "正在读取本地媒体信息"))
            duration = self.audio.probe_local_media(local_path)
            path_id = hashlib.sha256(str(local_path).casefold().encode("utf-8")).hexdigest()[:12]
            local_metadata = MediaMetadata(
                source_url="",
                platform="local_file",
                video_id=f"local_{path_id}",
                title=local_path.stem,
                duration=duration,
                is_live=False,
                is_playlist=False,
            )
            validate_media(local_metadata)
        elif options.input_type == "url":
            url = validate_url(options.url)
            on_progress(ProgressEvent("probe", "正在读取媒体信息"))
            probed_metadata = self.downloader.probe(url)
            validate_media(probed_metadata)
        else:
            raise InputError("不支持的输入类型。")
        self.audio.check_available()
        check_cancelled()

        self.temp_root.mkdir(parents=True, exist_ok=True)
        task_dir = Path(tempfile.mkdtemp(prefix="videotext-", dir=self.temp_root))
        succeeded = False
        try:
            if local_path is not None:
                on_progress(ProgressEvent("download", "正在读取本地媒体文件"))
                source = MediaSource(
                    local_metadata,
                    local_path,
                    {
                        "source_type": "local_file",
                        "file_name": local_path.name,
                        "duration": local_metadata.duration,
                    },
                )
            else:
                on_progress(ProgressEvent("download", "正在下载音频"))
                source = self.downloader.download(url, task_dir)
            validate_media(source.metadata)
            final_dir = output_directory(options.output_root, source.metadata)
            check_cancelled()

            on_progress(ProgressEvent("normalize", "正在标准化音频"))
            normalized = self.audio.normalize(
                source.media_path, task_dir / "normalized.wav"
            )
            check_cancelled()
            on_progress(ProgressEvent("transcribe", "正在使用 GPU 转写"))
            transcript = self.transcriber.transcribe(
                normalized, source.metadata, options.config
            )
            check_cancelled()
            on_progress(ProgressEvent("export", "正在写入结果"))
            paths = self.exporter(
                transcript,
                source,
                final_dir,
                options.overwrite,
                self.tool_version,
                normalized if options.keep_audio else None,
            )
            succeeded = True
            on_progress(ProgressEvent("done", "处理完成"))
            return paths
        finally:
            if succeeded:
                shutil.rmtree(task_dir, ignore_errors=True)
                try:
                    self.temp_root.rmdir()
                except OSError:
                    pass
