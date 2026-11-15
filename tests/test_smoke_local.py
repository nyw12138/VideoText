import os
from pathlib import Path

import pytest

from videotext.audio import FfmpegAudioNormalizer
from videotext.downloader import YtDlpDownloader
from videotext.exporters import export_transcript
from videotext.models import TranscriptionConfig
from videotext.service import RunOptions, VideoTextService
from videotext.transcriber import FasterWhisperTranscriber


@pytest.mark.skipif(
    not os.getenv("VIDEOTEXT_SMOKE_URL"), reason="set VIDEOTEXT_SMOKE_URL"
)
def test_authorized_public_video_end_to_end(tmp_path: Path):
    service = VideoTextService(
        YtDlpDownloader(),
        FfmpegAudioNormalizer(),
        FasterWhisperTranscriber(),
        export_transcript,
        tmp_path / "temp",
        "0.1.0",
    )
    paths = service.run(
        RunOptions(
            os.environ["VIDEOTEXT_SMOKE_URL"],
            tmp_path / "outputs",
            TranscriptionConfig(),
            False,
            False,
        ),
        lambda event: None,
    )
    assert {path.name for path in paths} == {
        "transcript.txt",
        "transcript.srt",
        "transcript.json",
        "source.json",
    }
    assert all(path.stat().st_size > 0 for path in paths)
