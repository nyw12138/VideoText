from dataclasses import replace
from pathlib import Path

import pytest

from videotext.errors import CancelledError, InputError, TranscriptionError
from videotext.models import (
    MediaMetadata,
    MediaSource,
    Transcript,
    TranscriptSegment,
    TranscriptionConfig,
)
from videotext.service import RunOptions, VideoTextService

META = MediaMetadata("https://example.com/1", "Example", "1", "Demo", 10.0, False, False)
TRANSCRIPT = Transcript(
    "https://example.com/1",
    "Example",
    "1",
    "Demo",
    10.0,
    "zh",
    0.9,
    "large-v3-turbo",
    "2026-07-13T00:00:00+00:00",
    (TranscriptSegment(0, 1, "你好"),),
)


class FakeDownloader:
    def __init__(self, downloaded_metadata=META):
        self.downloaded_metadata = downloaded_metadata

    def probe(self, url):
        return META

    def download(self, url, target):
        path = target / "downloaded.m4a"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"audio")
        return MediaSource(self.downloaded_metadata, path, {"id": "1"})


class FakeAudio:
    def __init__(self):
        self.normalize_calls = 0

    def check_available(self):
        return None

    def normalize(self, source, destination):
        self.normalize_calls += 1
        destination.write_bytes(b"wav")
        return destination


class FakeTranscriber:
    def __init__(self, error=None):
        self.error = error

    def transcribe(self, audio, metadata, config):
        if self.error:
            raise self.error
        return TRANSCRIPT


def fake_export(transcript, source, output_dir, overwrite, tool_version, audio_path=None):
    output_dir.mkdir(parents=True, exist_ok=True)
    result = output_dir / "transcript.txt"
    result.write_text("你好\n", encoding="utf-8")
    paths = [result]
    if audio_path is not None:
        saved_audio = output_dir / "audio.wav"
        saved_audio.write_bytes(audio_path.read_bytes())
        paths.append(saved_audio)
    return tuple(paths)


def options(tmp_path: Path, keep_audio=False):
    return RunOptions(
        "https://example.com/1",
        tmp_path / "outputs",
        TranscriptionConfig(),
        keep_audio,
        False,
    )


def test_success_runs_stages_and_removes_temp_directory(tmp_path: Path):
    events = []
    service = VideoTextService(
        FakeDownloader(),
        FakeAudio(),
        FakeTranscriber(),
        fake_export,
        tmp_path / "temp",
        "0.1.0",
    )
    paths = service.run(options(tmp_path), events.append)
    assert paths[0].read_text(encoding="utf-8") == "你好\n"
    assert [event.stage for event in events] == [
        "probe",
        "download",
        "normalize",
        "transcribe",
        "export",
        "done",
    ]
    assert not (tmp_path / "temp").exists()


def test_keep_audio_is_part_of_exported_output(tmp_path: Path):
    service = VideoTextService(
        FakeDownloader(),
        FakeAudio(),
        FakeTranscriber(),
        fake_export,
        tmp_path / "temp",
        "0.1.0",
    )
    paths = service.run(options(tmp_path, keep_audio=True), lambda event: None)
    assert {path.name for path in paths} == {"transcript.txt", "audio.wav"}
    assert (tmp_path / "outputs" / "Demo_1" / "audio.wav").is_file()


def test_failure_preserves_task_temp_and_writes_no_final_output(tmp_path: Path):
    service = VideoTextService(
        FakeDownloader(),
        FakeAudio(),
        FakeTranscriber(TranscriptionError("boom")),
        fake_export,
        tmp_path / "temp",
        "0.1.0",
    )
    with pytest.raises(TranscriptionError):
        service.run(options(tmp_path), lambda event: None)
    assert list((tmp_path / "temp").glob("*/normalized.wav"))
    assert not (tmp_path / "outputs" / "Demo_1").exists()


def test_downloaded_metadata_is_revalidated_before_normalizing(tmp_path: Path):
    audio = FakeAudio()
    downloader = FakeDownloader(replace(META, duration=10801.0))
    service = VideoTextService(
        downloader,
        audio,
        FakeTranscriber(),
        fake_export,
        tmp_path / "temp",
        "0.1.0",
    )
    with pytest.raises(InputError, match="3 小时"):
        service.run(options(tmp_path), lambda event: None)
    assert audio.normalize_calls == 0


def test_cancel_before_download_stops_at_stage_boundary(tmp_path: Path):
    events = []
    service = VideoTextService(
        FakeDownloader(),
        FakeAudio(),
        FakeTranscriber(),
        fake_export,
        tmp_path / "temp",
        "0.1.0",
    )
    with pytest.raises(CancelledError):
        service.run(options(tmp_path), events.append, lambda: True)
    assert [event.stage for event in events] == ["probe"]
    assert not (tmp_path / "outputs").exists()


def test_existing_two_argument_run_remains_compatible(tmp_path: Path):
    service = VideoTextService(
        FakeDownloader(),
        FakeAudio(),
        FakeTranscriber(),
        fake_export,
        tmp_path / "temp",
        "0.1.0",
    )
    assert (
        service.run(options(tmp_path), lambda event: None)[0].name
        == "transcript.txt"
    )
