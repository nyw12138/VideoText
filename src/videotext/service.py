import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .errors import CancelledError
from .models import MediaMetadata, MediaSource, Transcript, TranscriptionConfig
from .paths import output_directory
from .validation import validate_media, validate_url


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


class Downloader(Protocol):
    def probe(self, url: str) -> MediaMetadata: ...

    def download(self, url: str, target_dir: Path) -> MediaSource: ...


class AudioNormalizer(Protocol):
    def check_available(self) -> None: ...

    def normalize(self, source: Path, destination: Path) -> Path: ...


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

        url = validate_url(options.url)
        on_progress(ProgressEvent("probe", "正在读取媒体信息"))
        probed_metadata = self.downloader.probe(url)
        validate_media(probed_metadata)
        self.audio.check_available()
        check_cancelled()

        self.temp_root.mkdir(parents=True, exist_ok=True)
        task_dir = Path(tempfile.mkdtemp(prefix="videotext-", dir=self.temp_root))
        succeeded = False
        try:
            on_progress(ProgressEvent("download", "正在下载音频"))
            source = self.downloader.download(url, task_dir)
            validate_media(source.metadata)
            final_dir = output_directory(options.output_root, source.metadata)
            check_cancelled()

            on_progress(ProgressEvent("normalize", "正在标准化音频"))
            normalized = self.audio.normalize(
                source.downloaded_audio_path, task_dir / "normalized.wav"
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
