from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class MediaMetadata:
    source_url: str
    platform: str
    video_id: str
    title: str
    duration: float | None
    is_live: bool
    is_playlist: bool


@dataclass(frozen=True, slots=True)
class MediaSource:
    metadata: MediaMetadata
    downloaded_audio_path: Path
    raw_metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    start: float
    end: float
    text: str


@dataclass(frozen=True, slots=True)
class Transcript:
    source_url: str
    platform: str
    video_id: str
    title: str
    duration: float
    detected_language: str
    language_probability: float
    model: str
    created_at: str
    segments: tuple[TranscriptSegment, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TranscriptionConfig:
    model: str = "large-v3-turbo"
    device: str = "cuda"
    compute_type: str = "float16"
    language: str | None = None
    vad_filter: bool = True
