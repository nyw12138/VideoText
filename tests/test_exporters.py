import json
import os
from pathlib import Path

import pytest

from videotext.errors import ExportError
from videotext.exporters import export_transcript, format_srt_timestamp
from videotext.models import MediaMetadata, MediaSource, Transcript, TranscriptSegment


def transcript():
    return Transcript(
        source_url="https://example.com/1",
        platform="Example",
        video_id="1",
        title="演示",
        duration=3661.25,
        detected_language="zh",
        language_probability=0.99,
        model="large-v3-turbo",
        created_at="2026-07-13T10:00:00+00:00",
        segments=(
            TranscriptSegment(0.0, 1.25, " 你好 "),
            TranscriptSegment(3600.0, 3661.25, " world "),
        ),
    )


def source(tmp_path: Path):
    meta = MediaMetadata(
        "https://example.com/1", "Example", "1", "演示", 3661.25, False, False
    )
    raw = {"id": "1", "http_headers": {"Cookie": "secret"}, "nested": {"token": "x"}}
    return MediaSource(meta, tmp_path / "audio.wav", raw)


def test_format_srt_timestamp_handles_hours_and_milliseconds():
    assert format_srt_timestamp(3661.25) == "01:01:01,250"


def test_export_transcript_writes_consistent_utf8_outputs(tmp_path: Path):
    paths = export_transcript(transcript(), source(tmp_path), tmp_path / "out", False, "0.1.0")
    assert {p.name for p in paths} == {
        "transcript.txt",
        "transcript.srt",
        "transcript.json",
        "source.json",
    }
    assert (tmp_path / "out" / "transcript.txt").read_text(encoding="utf-8") == "你好\nworld\n"
    srt = (tmp_path / "out" / "transcript.srt").read_text(encoding="utf-8")
    assert "00:00:00,000 --> 00:00:01,250\n你好" in srt
    assert "01:00:00,000 --> 01:01:01,250\nworld" in srt
    data = json.loads((tmp_path / "out" / "transcript.json").read_text(encoding="utf-8"))
    assert [part["text"].strip() for part in data["segments"]] == ["你好", "world"]
    source_data = json.loads((tmp_path / "out" / "source.json").read_text(encoding="utf-8"))
    assert "http_headers" not in source_data["metadata"]
    assert "token" not in source_data["metadata"]["nested"]


def test_export_can_atomically_include_audio(tmp_path: Path):
    audio = tmp_path / "normalized.wav"
    audio.write_bytes(b"wave")
    paths = export_transcript(
        transcript(), source(tmp_path), tmp_path / "out", False, "0.1.0", audio
    )
    assert (tmp_path / "out" / "audio.wav").read_bytes() == b"wave"
    assert {path.name for path in paths} == {
        "transcript.txt",
        "transcript.srt",
        "transcript.json",
        "source.json",
        "audio.wav",
    }


def test_export_refuses_existing_output_without_overwrite(tmp_path: Path):
    output = tmp_path / "out"
    export_transcript(transcript(), source(tmp_path), output, False, "0.1.0")
    with pytest.raises(ExportError, match="已存在"):
        export_transcript(transcript(), source(tmp_path), output, False, "0.1.0")


def test_failed_serialization_leaves_no_final_directory(tmp_path: Path, monkeypatch):
    output = tmp_path / "out"

    def fail(*args, **kwargs):
        raise TypeError("boom")

    monkeypatch.setattr("videotext.exporters.json.dumps", fail)
    with pytest.raises(ExportError):
        export_transcript(transcript(), source(tmp_path), output, False, "0.1.0")
    assert not output.exists()


def test_failed_atomic_swap_restores_previous_output(tmp_path: Path, monkeypatch):
    output = tmp_path / "out"
    export_transcript(transcript(), source(tmp_path), output, False, "0.1.0")
    (output / "transcript.txt").write_text("old\n", encoding="utf-8")
    real_replace = os.replace
    calls = 0

    def fail_second_replace(source_path, destination_path):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("swap failed")
        return real_replace(source_path, destination_path)

    monkeypatch.setattr("videotext.exporters.os.replace", fail_second_replace)
    with pytest.raises(ExportError, match="swap failed"):
        export_transcript(transcript(), source(tmp_path), output, True, "0.1.0")
    assert (output / "transcript.txt").read_text(encoding="utf-8") == "old\n"
