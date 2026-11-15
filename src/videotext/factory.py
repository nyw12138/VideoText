from pathlib import Path

from . import __version__
from .audio import FfmpegAudioNormalizer
from .downloader import YtDlpDownloader
from .exporters import export_transcript
from .service import VideoTextService
from .transcriber import FasterWhisperTranscriber


def build_service(temp_root: Path) -> VideoTextService:
    return VideoTextService(
        downloader=YtDlpDownloader(),
        audio=FfmpegAudioNormalizer(),
        transcriber=FasterWhisperTranscriber(),
        exporter=export_transcript,
        temp_root=temp_root,
        tool_version=__version__,
    )
