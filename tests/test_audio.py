from pathlib import Path
from subprocess import CompletedProcess

import pytest

from videotext.audio import FfmpegAudioNormalizer
from videotext.errors import EnvironmentError, TranscriptionError


def test_check_available_calls_ffmpeg_version_without_shell():
    calls = []

    def runner(*args, **kwargs):
        calls.append((args, kwargs))
        return CompletedProcess(args[0], 0, "", "")

    FfmpegAudioNormalizer(runner=runner).check_available()
    assert calls[0][0][0] == ["ffmpeg", "-version"]
    assert calls[0][1]["shell"] is False


def test_missing_ffmpeg_is_environment_error():
    def missing(*args, **kwargs):
        raise FileNotFoundError("ffmpeg")

    with pytest.raises(EnvironmentError, match="FFmpeg"):
        FfmpegAudioNormalizer(runner=missing).check_available()


def test_normalize_builds_16khz_mono_command_and_returns_file(tmp_path: Path):
    source = tmp_path / "input.m4a"
    destination = tmp_path / "normalized.wav"
    source.write_bytes(b"input")
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        destination.write_bytes(b"wav")
        return CompletedProcess(command, 0, "", "")

    result = FfmpegAudioNormalizer(runner=runner).normalize(source, destination)
    assert result == destination
    assert calls[0][0] == [
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
    assert calls[0][1]["shell"] is False


def test_normalize_failure_preserves_source_and_has_diagnostic(tmp_path: Path):
    source = tmp_path / "input.m4a"
    source.write_bytes(b"input")

    def runner(command, **kwargs):
        return CompletedProcess(command, 1, "", "invalid audio")

    with pytest.raises(TranscriptionError, match="invalid audio"):
        FfmpegAudioNormalizer(runner=runner).normalize(source, tmp_path / "normalized.wav")
    assert source.exists()
