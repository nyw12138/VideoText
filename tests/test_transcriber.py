from pathlib import Path
from types import SimpleNamespace

import pytest

from videotext.errors import EnvironmentError, TranscriptionError
from videotext.models import MediaMetadata, TranscriptionConfig
from videotext.transcriber import FasterWhisperTranscriber, default_model_factory


def metadata():
    return MediaMetadata("https://example.com/1", "Example", "1", "Demo", 10.0, False, False)


class FakeModel:
    def __init__(self, segments=None, error=None):
        self.segments = segments or []
        self.error = error
        self.calls = []

    def transcribe(self, path, **kwargs):
        self.calls.append((path, kwargs))
        if self.error:
            raise self.error
        return iter(self.segments), SimpleNamespace(language="zh", language_probability=0.98)


def test_transcribe_uses_cuda_fp16_vad_and_collects_segments(tmp_path: Path):
    created = {}
    model = FakeModel([SimpleNamespace(start=0.0, end=1.0, text=" 你好 ")])

    def factory(name, **kwargs):
        created.update(name=name, **kwargs)
        return model

    result = FasterWhisperTranscriber(model_factory=factory).transcribe(
        tmp_path / "audio.wav", metadata(), TranscriptionConfig()
    )
    assert created == {
        "name": "large-v3-turbo",
        "device": "cuda",
        "compute_type": "float16",
    }
    assert model.calls[0][1] == {"language": None, "vad_filter": True}
    assert result.detected_language == "zh"
    assert result.segments[0].text == "你好"


def test_language_override_is_forwarded(tmp_path: Path):
    model = FakeModel([SimpleNamespace(start=0.0, end=1.0, text="hello")])
    transcriber = FasterWhisperTranscriber(model_factory=lambda *args, **kwargs: model)
    transcriber.transcribe(tmp_path / "audio.wav", metadata(), TranscriptionConfig(language="en"))
    assert model.calls[0][1]["language"] == "en"


def test_empty_speech_is_transcription_error(tmp_path: Path):
    transcriber = FasterWhisperTranscriber(model_factory=lambda *args, **kwargs: FakeModel())
    with pytest.raises(TranscriptionError, match="有效语音"):
        transcriber.transcribe(tmp_path / "audio.wav", metadata(), TranscriptionConfig())


def test_non_monotonic_timestamps_are_rejected(tmp_path: Path):
    model = FakeModel(
        [
            SimpleNamespace(start=1.0, end=2.0, text="one"),
            SimpleNamespace(start=1.5, end=3.0, text="overlap"),
        ]
    )
    with pytest.raises(TranscriptionError, match="时间戳"):
        FasterWhisperTranscriber(model_factory=lambda *args, **kwargs: model).transcribe(
            tmp_path / "audio.wav", metadata(), TranscriptionConfig()
        )


def test_cuda_failure_does_not_fall_back_to_cpu(tmp_path: Path):
    calls = []

    def factory(name, **kwargs):
        calls.append(kwargs)
        raise RuntimeError("CUDA out of memory")

    with pytest.raises(TranscriptionError, match="CUDA out of memory") as caught:
        FasterWhisperTranscriber(model_factory=factory).transcribe(
            tmp_path / "audio.wav", metadata(), TranscriptionConfig()
        )
    assert calls == [{"device": "cuda", "compute_type": "float16"}]
    assert "较小模型" in caught.value.hint


def test_default_factory_reports_missing_dependency(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def missing(name, *args, **kwargs):
        if name == "faster_whisper":
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)
    with pytest.raises(EnvironmentError, match="faster-whisper"):
        default_model_factory("large-v3-turbo", device="cuda", compute_type="float16")
