# Video-to-Text CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Windows-local Python CLI that accepts one public Bilibili, Douyin, or YouTube video URL, transcribes its audio on an NVIDIA GPU, and atomically writes UTF-8 TXT, SRT, JSON, and source metadata files.

**Architecture:** A thin Typer CLI calls an application service composed from isolated downloader, audio normalizer, transcriber, and exporter adapters. Domain dataclasses and typed errors keep third-party details out of orchestration; tests inject fakes so network, FFmpeg, and GPU behavior can be verified independently.

**Tech Stack:** Python 3.11+, Typer, Rich, yt-dlp Python API, FFmpeg/ffprobe executables, faster-whisper/CTranslate2, pytest, Ruff.

## Global Constraints

- Run locally on Windows with Python 3.11 or newer and an NVIDIA GPU.
- Accept exactly one public HTTP/HTTPS on-demand video URL.
- Reject playlists, livestreams, unknown-duration sources, and videos longer than 7200 seconds.
- Do not support cookies, login, CAPTCHA, paywalls, DRM, or region-control bypasses.
- Default to `large-v3-turbo`, `device="cuda"`, `compute_type="float16"`, automatic language detection, and VAD enabled.
- Never silently fall back from CUDA to CPU.
- Write UTF-8 TXT, SRT, transcript JSON, and source JSON from one shared transcript object.
- Write final artifacts atomically; failed runs must not appear complete.
- Preserve temporary audio after failures; remove it only after success unless `--keep-audio` is set.
- Do not execute user-provided URLs through a shell.
- Platform support is best-effort through the installed yt-dlp version.
- Standard Git commits in this plan require a writable `.git` directory; the current sandbox blocks `.git` writes, so execute commit steps in a normal project checkout or after that permission is fixed.

---

## Planned File Structure

```text
pyproject.toml                         package metadata, dependencies, CLI entry point
README.md                              installation and usage instructions
src/videotext/__init__.py              package version
src/videotext/models.py                immutable domain records
src/videotext/errors.py                typed domain failures and exit codes
src/videotext/validation.py            URL and media policy checks
src/videotext/paths.py                 Windows-safe task and output paths
src/videotext/exporters.py             TXT/SRT/JSON atomic serialization
src/videotext/downloader.py            yt-dlp metadata and audio adapter
src/videotext/audio.py                 FFmpeg availability and normalization
src/videotext/transcriber.py           faster-whisper GPU adapter
src/videotext/service.py               end-to-end orchestration and cleanup policy
src/videotext/cli.py                   Typer command and user-facing diagnostics
tests/test_validation.py               input and media policy tests
tests/test_paths.py                    filename and directory tests
tests/test_exporters.py                output consistency and atomicity tests
tests/test_downloader.py               mocked yt-dlp adapter tests
tests/test_audio.py                    mocked subprocess tests
tests/test_transcriber.py              mocked faster-whisper tests
tests/test_service.py                  orchestration and cleanup tests
tests/test_cli.py                      CLI behavior and exit-code tests
tests/test_smoke_local.py               opt-in local external-tool smoke tests
```

Each source file owns one responsibility. Third-party libraries are imported only by their adapter modules, allowing all ordinary tests to run without network access, FFmpeg, model downloads, or a GPU.

---

### Task 1: Package Skeleton, Domain Models, Validation, and Safe Paths

**Files:**
- Create: `pyproject.toml`
- Create: `src/videotext/__init__.py`
- Create: `src/videotext/models.py`
- Create: `src/videotext/errors.py`
- Create: `src/videotext/validation.py`
- Create: `src/videotext/paths.py`
- Create: `tests/test_validation.py`
- Create: `tests/test_paths.py`

**Interfaces:**
- Produces: `MediaMetadata`, `MediaSource`, `TranscriptSegment`, `Transcript`, and `TranscriptionConfig` dataclasses.
- Produces: `VideoTextError(message, hint, exit_code)`, plus typed subclasses.
- Produces: `validate_url(url: str) -> str`, `validate_media(metadata: MediaMetadata, max_duration: float = 7200.0) -> None`.
- Produces: `safe_component(value: str, max_length: int = 100) -> str` and `output_directory(root: Path, metadata: MediaMetadata) -> Path`.

- [ ] **Step 1: Add package metadata and the failing validation/path tests**

```toml
# pyproject.toml
[build-system]
requires = ["hatchling>=1.25,<2"]
build-backend = "hatchling.build"

[project]
name = "videotext"
version = "0.1.0"
description = "Transcribe public video links locally with faster-whisper"
requires-python = ">=3.11"
dependencies = [
  "faster-whisper>=1.1,<2",
  "rich>=13,<15",
  "typer>=0.12,<1",
  "yt-dlp>=2025.1",
]

[project.optional-dependencies]
dev = ["pytest>=8,<10", "ruff>=0.9,<1"]

[project.scripts]
videotext = "videotext.cli:app"

[tool.hatch.build.targets.wheel]
packages = ["src/videotext"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"

[tool.ruff]
line-length = 100
target-version = "py311"
```

```python
# tests/test_validation.py
import pytest

from videotext.errors import InputError
from videotext.models import MediaMetadata
from videotext.validation import validate_media, validate_url


def metadata(**overrides):
    values = dict(
        source_url="https://example.com/watch/1",
        platform="Example",
        video_id="1",
        title="Demo",
        duration=60.0,
        is_live=False,
        is_playlist=False,
    )
    values.update(overrides)
    return MediaMetadata(**values)


@pytest.mark.parametrize("url", ["file:///tmp/a.mp4", "javascript:alert(1)", "not-a-url"])
def test_validate_url_rejects_non_http_urls(url):
    with pytest.raises(InputError):
        validate_url(url)


def test_validate_url_accepts_https_and_strips_outer_whitespace():
    assert validate_url("  https://example.com/video/1  ") == "https://example.com/video/1"


@pytest.mark.parametrize(
    ("item", "message"),
    [
        (metadata(is_playlist=True), "播放列表"),
        (metadata(is_live=True), "直播"),
        (metadata(duration=None), "时长"),
        (metadata(duration=7200.01), "2 小时"),
    ],
)
def test_validate_media_rejects_out_of_scope_sources(item, message):
    with pytest.raises(InputError, match=message):
        validate_media(item)


@pytest.mark.parametrize("duration", [0.0, 7199.0, 7200.0])
def test_validate_media_accepts_duration_boundary(duration):
    validate_media(metadata(duration=duration))
```

```python
# tests/test_paths.py
from pathlib import Path

from videotext.models import MediaMetadata
from videotext.paths import output_directory, safe_component


def test_safe_component_removes_windows_invalid_characters_and_trailing_dots():
    assert safe_component(' A<B>:"C/D\\E|F?G*... ') == "A_B_C_D_E_F_G"


def test_safe_component_handles_reserved_name():
    assert safe_component("CON") == "_CON"


def test_output_directory_includes_safe_title_and_video_id(tmp_path: Path):
    item = MediaMetadata(
        source_url="https://example.com/1",
        platform="Example",
        video_id="abc/123",
        title="标题:演示",
        duration=20.0,
        is_live=False,
        is_playlist=False,
    )
    assert output_directory(tmp_path, item) == tmp_path / "标题_演示_abc_123"
```

- [ ] **Step 2: Run the focused tests and verify collection fails**

Run: `python -m pytest tests/test_validation.py tests/test_paths.py -q`

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'videotext'`.

- [ ] **Step 3: Implement the package, models, errors, validation, and path rules**

```python
# src/videotext/__init__.py
__version__ = "0.1.0"
```

```python
# src/videotext/models.py
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
```

```python
# src/videotext/errors.py
class VideoTextError(Exception):
    def __init__(self, message: str, hint: str, exit_code: int):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.exit_code = exit_code


class InputError(VideoTextError):
    def __init__(self, message: str, hint: str = "请检查链接和参数。"):
        super().__init__(message, hint, 2)


class PlatformError(VideoTextError):
    def __init__(self, message: str, hint: str = "请更新 yt-dlp 或改用其他公开视频。"):
        super().__init__(message, hint, 3)


class DownloadError(VideoTextError):
    def __init__(self, message: str, hint: str = "请检查网络和磁盘空间后重试。"):
        super().__init__(message, hint, 4)


class EnvironmentError(VideoTextError):
    def __init__(self, message: str, hint: str = "请检查 FFmpeg、CUDA 和运行库安装。"):
        super().__init__(message, hint, 5)


class TranscriptionError(VideoTextError):
    def __init__(self, message: str, hint: str = "请检查音频、模型和 GPU 显存。"):
        super().__init__(message, hint, 6)


class ExportError(VideoTextError):
    def __init__(self, message: str, hint: str = "请检查输出路径权限和覆盖选项。"):
        super().__init__(message, hint, 7)
```

```python
# src/videotext/validation.py
from urllib.parse import urlparse

from .errors import InputError
from .models import MediaMetadata


def validate_url(url: str) -> str:
    value = url.strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise InputError("链接必须是有效的 HTTP 或 HTTPS URL。")
    return value


def validate_media(metadata: MediaMetadata, max_duration: float = 7200.0) -> None:
    if metadata.is_playlist:
        raise InputError("第一版不支持播放列表。")
    if metadata.is_live:
        raise InputError("第一版不支持直播。")
    if metadata.duration is None:
        raise InputError("无法确认视频时长，因此拒绝处理。")
    if metadata.duration > max_duration:
        raise InputError("视频超过 2 小时时长限制。")
```

```python
# src/videotext/paths.py
import re
from pathlib import Path

from .models import MediaMetadata

INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def safe_component(value: str, max_length: int = 100) -> str:
    result = INVALID.sub("_", value).strip().rstrip(". ")
    result = re.sub(r"_+", "_", result).strip(" ._")
    if not result:
        result = "untitled"
    if result.upper() in RESERVED:
        result = f"_{result}"
    return result[:max_length].rstrip(". ") or "untitled"


def output_directory(root: Path, metadata: MediaMetadata) -> Path:
    title = safe_component(metadata.title)
    video_id = safe_component(metadata.video_id, max_length=50)
    return root / f"{title}_{video_id}"
```

- [ ] **Step 4: Install editable dependencies and run tests**

Run: `python -m pip install -e ".[dev]"`

Expected: exit code 0 and an editable `videotext` package installed.

Run: `python -m pytest tests/test_validation.py tests/test_paths.py -q`

Expected: all tests PASS.

- [ ] **Step 5: Run lint and commit**

Run: `python -m ruff check src tests`

Expected: `All checks passed!`

```bash
git add pyproject.toml src/videotext tests/test_validation.py tests/test_paths.py
git commit -m "feat: add video transcription domain foundation"
```

---

### Task 2: Consistent Atomic TXT, SRT, and JSON Export

**Files:**
- Create: `src/videotext/exporters.py`
- Create: `tests/test_exporters.py`

**Interfaces:**
- Consumes: `Transcript`, `TranscriptSegment`, `MediaSource` from Task 1.
- Produces: `format_srt_timestamp(seconds: float) -> str`.
- Produces: `export_transcript(transcript: Transcript, source: MediaSource, output_dir: Path, overwrite: bool, tool_version: str, audio_path: Path | None = None) -> tuple[Path, ...]`.

- [ ] **Step 1: Write failing exporter tests**

```python
# tests/test_exporters.py
import json
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
    meta = MediaMetadata("https://example.com/1", "Example", "1", "演示", 3661.25, False, False)
    return MediaSource(meta, tmp_path / "audio.wav", {"id": "1", "http_headers": {"Cookie": "secret"}})


def test_format_srt_timestamp_handles_hours_and_milliseconds():
    assert format_srt_timestamp(3661.25) == "01:01:01,250"


def test_export_transcript_writes_consistent_utf8_outputs(tmp_path: Path):
    paths = export_transcript(transcript(), source(tmp_path), tmp_path / "out", False, "0.1.0")
    assert {p.name for p in paths} == {"transcript.txt", "transcript.srt", "transcript.json", "source.json"}
    assert (tmp_path / "out" / "transcript.txt").read_text(encoding="utf-8") == "你好\nworld\n"
    srt = (tmp_path / "out" / "transcript.srt").read_text(encoding="utf-8")
    assert "00:00:00,000 --> 00:00:01,250\n你好" in srt
    assert "01:00:00,000 --> 01:01:01,250\nworld" in srt
    data = json.loads((tmp_path / "out" / "transcript.json").read_text(encoding="utf-8"))
    assert [part["text"].strip() for part in data["segments"]] == ["你好", "world"]
    source_data = json.loads((tmp_path / "out" / "source.json").read_text(encoding="utf-8"))
    assert "http_headers" not in source_data["metadata"]


def test_export_refuses_existing_output_without_overwrite(tmp_path: Path):
    output = tmp_path / "out"
    export_transcript(transcript(), source(tmp_path), output, False, "0.1.0")
    with pytest.raises(ExportError, match="已存在"):
        export_transcript(transcript(), source(tmp_path), output, False, "0.1.0")


def test_failed_serialization_leaves_no_final_files(tmp_path: Path, monkeypatch):
    output = tmp_path / "out"
    monkeypatch.setattr("videotext.exporters.json.dumps", lambda *args, **kwargs: (_ for _ in ()).throw(TypeError("boom")))
    with pytest.raises(ExportError):
        export_transcript(transcript(), source(tmp_path), output, False, "0.1.0")
    assert not list(output.glob("transcript.*"))


def test_failed_atomic_swap_restores_previous_output(tmp_path: Path, monkeypatch):
    import os
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
```

- [ ] **Step 2: Run exporter tests and verify failure**

Run: `python -m pytest tests/test_exporters.py -q`

Expected: FAIL during collection with `No module named 'videotext.exporters'`.

- [ ] **Step 3: Implement sanitized, atomic export**

```python
# src/videotext/exporters.py
import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

from .errors import ExportError
from .models import MediaSource, Transcript


SENSITIVE_KEYS = {"cookie", "cookies", "http_headers", "authorization", "token"}


def format_srt_timestamp(seconds: float) -> str:
    total_ms = max(0, round(seconds * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items() if key.lower() not in SENSITIVE_KEYS}
    if isinstance(value, list):
        return [_clean(item) for item in value]
    return value


def _payloads(transcript: Transcript, source: MediaSource, tool_version: str) -> dict[str, str]:
    text = "".join(f"{segment.text.strip()}\n" for segment in transcript.segments if segment.text.strip())
    blocks = []
    for index, segment in enumerate(transcript.segments, 1):
        blocks.append(
            f"{index}\n{format_srt_timestamp(segment.start)} --> "
            f"{format_srt_timestamp(segment.end)}\n{segment.text.strip()}\n"
        )
    transcript_json = json.dumps(transcript.to_dict(), ensure_ascii=False, indent=2) + "\n"
    source_json = json.dumps(
        {"tool_version": tool_version, "metadata": _clean(source.raw_metadata)},
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    return {
        "transcript.txt": text,
        "transcript.srt": "\n".join(blocks),
        "transcript.json": transcript_json,
        "source.json": source_json,
    }


def export_transcript(
    transcript: Transcript,
    source: MediaSource,
    output_dir: Path,
    overwrite: bool,
    tool_version: str,
    audio_path: Path | None = None,
) -> tuple[Path, ...]:
    final_names = ["transcript.txt", "transcript.srt", "transcript.json", "source.json"]
    if audio_path is not None:
        final_names.append("audio.wav")
    if output_dir.exists() and not overwrite:
        raise ExportError("输出文件已存在。", "请更换目录或使用 --overwrite。")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}-", dir=output_dir.parent))
    backup: Path | None = None
    try:
        payloads = _payloads(transcript, source, tool_version)
        for name, content in payloads.items():
            (staging / name).write_text(content, encoding="utf-8", newline="\n")
        if audio_path is not None:
            shutil.copy2(audio_path, staging / "audio.wav")
        if output_dir.exists():
            backup = output_dir.parent / f".{output_dir.name}.backup-{uuid.uuid4().hex}"
            os.replace(output_dir, backup)
        os.replace(staging, output_dir)
        if backup is not None:
            shutil.rmtree(backup, ignore_errors=True)
        return tuple(output_dir / name for name in final_names)
    except ExportError:
        raise
    except Exception as exc:
        if backup is not None and backup.exists() and not output_dir.exists():
            os.replace(backup, output_dir)
        raise ExportError(f"写入输出失败：{exc}") from exc
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
```

- [ ] **Step 4: Run exporter and regression tests**

Run: `python -m pytest tests/test_exporters.py tests/test_validation.py tests/test_paths.py -q`

Expected: all tests PASS.

- [ ] **Step 5: Lint and commit**

Run: `python -m ruff check src tests`

Expected: `All checks passed!`

```bash
git add src/videotext/exporters.py tests/test_exporters.py
git commit -m "feat: export atomic transcript artifacts"
```

---

### Task 3: yt-dlp Metadata Probe and Audio Download Adapter

**Files:**
- Create: `src/videotext/downloader.py`
- Create: `tests/test_downloader.py`

**Interfaces:**
- Consumes: validated URL strings and Task 1 domain errors/models.
- Produces: `YtDlpDownloader.probe(url: str) -> MediaMetadata`.
- Produces: `YtDlpDownloader.download(url: str, target_dir: Path) -> MediaSource`.

- [ ] **Step 1: Write failing downloader tests with a fake YoutubeDL**

```python
# tests/test_downloader.py
from pathlib import Path

import pytest

from videotext.downloader import YtDlpDownloader
from videotext.errors import DownloadError, PlatformError


class FakeYDL:
    def __init__(self, options, result=None, error=None):
        self.options = options
        self.result = result
        self.error = error

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def extract_info(self, url, download):
        if self.error:
            raise self.error
        return self.result

    def prepare_filename(self, info):
        return str(Path(self.options["paths"]["home"]) / "downloaded.m4a")


def info(**overrides):
    value = {"id": "abc", "title": "Demo", "duration": 60.0, "extractor_key": "Youtube", "webpage_url": "https://example.com/abc", "_type": "video", "is_live": False, "ext": "m4a"}
    value.update(overrides)
    return value


def test_probe_maps_metadata_without_downloading():
    holder = {}
    downloader = YtDlpDownloader(ydl_factory=lambda options: holder.setdefault("ydl", FakeYDL(options, info())))
    result = downloader.probe("https://example.com/abc")
    assert result.video_id == "abc"
    assert result.platform == "Youtube"
    assert holder["ydl"].options["skip_download"] is True


def test_probe_marks_playlist():
    downloader = YtDlpDownloader(ydl_factory=lambda options: FakeYDL(options, info(_type="playlist")))
    assert downloader.probe("https://example.com/list").is_playlist is True


def test_download_selects_audio_only_and_returns_existing_path(tmp_path: Path):
    def factory(options):
        audio = tmp_path / "downloaded.m4a"
        audio.write_bytes(b"audio")
        return FakeYDL(options, info())
    downloader = YtDlpDownloader(ydl_factory=factory)
    source = downloader.download("https://example.com/abc", tmp_path)
    assert source.downloaded_audio_path == tmp_path / "downloaded.m4a"


def test_expected_extractor_error_becomes_platform_error():
    from yt_dlp.utils import DownloadError as YtDownloadError
    downloader = YtDlpDownloader(ydl_factory=lambda options: FakeYDL(options, error=YtDownloadError("login required")))
    with pytest.raises(PlatformError, match="解析"):
        downloader.probe("https://example.com/private")


def test_missing_downloaded_file_becomes_download_error(tmp_path: Path):
    downloader = YtDlpDownloader(ydl_factory=lambda options: FakeYDL(options, info()))
    with pytest.raises(DownloadError, match="音频文件"):
        downloader.download("https://example.com/abc", tmp_path)
```

- [ ] **Step 2: Run downloader tests and verify failure**

Run: `python -m pytest tests/test_downloader.py -q`

Expected: FAIL during collection with `No module named 'videotext.downloader'`.

- [ ] **Step 3: Implement the downloader without shell execution**

```python
# src/videotext/downloader.py
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yt_dlp
from yt_dlp.utils import DownloadError as YtDownloadError

from .errors import DownloadError, PlatformError
from .models import MediaMetadata, MediaSource


YdlFactory = Callable[[dict[str, Any]], Any]


class YtDlpDownloader:
    def __init__(self, ydl_factory: YdlFactory = yt_dlp.YoutubeDL):
        self.ydl_factory = ydl_factory

    @staticmethod
    def _metadata(url: str, info: dict[str, Any]) -> MediaMetadata:
        return MediaMetadata(
            source_url=str(info.get("webpage_url") or url),
            platform=str(info.get("extractor_key") or info.get("extractor") or "unknown"),
            video_id=str(info.get("id") or "unknown"),
            title=str(info.get("title") or "untitled"),
            duration=float(info["duration"]) if info.get("duration") is not None else None,
            is_live=bool(info.get("is_live") or info.get("live_status") == "is_live"),
            is_playlist=info.get("_type") in {"playlist", "multi_video"},
        )

    def probe(self, url: str) -> MediaMetadata:
        options = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}
        try:
            with self.ydl_factory(options) as ydl:
                info = ydl.extract_info(url, download=False)
            return self._metadata(url, info)
        except YtDownloadError as exc:
            raise PlatformError(f"平台解析失败：{exc}") from exc
        except Exception as exc:
            raise PlatformError(f"读取媒体信息失败：{exc}") from exc

    def download(self, url: str, target_dir: Path) -> MediaSource:
        target_dir.mkdir(parents=True, exist_ok=True)
        options = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "format": "bestaudio/best",
            "paths": {"home": str(target_dir)},
            "outtmpl": {"default": "downloaded.%(ext)s"},
            "restrictfilenames": False,
        }
        try:
            with self.ydl_factory(options) as ydl:
                info = ydl.extract_info(url, download=True)
                path = Path(ydl.prepare_filename(info))
            if not path.is_file():
                raise DownloadError("下载器未生成预期的音频文件。")
            return MediaSource(self._metadata(url, info), path, info)
        except DownloadError:
            raise
        except YtDownloadError as exc:
            raise DownloadError(f"音频下载失败：{exc}") from exc
        except Exception as exc:
            raise DownloadError(f"音频下载失败：{exc}") from exc
```

- [ ] **Step 4: Run downloader and domain tests**

Run: `python -m pytest tests/test_downloader.py tests/test_validation.py -q`

Expected: all tests PASS.

- [ ] **Step 5: Lint and commit**

Run: `python -m ruff check src tests`

Expected: `All checks passed!`

```bash
git add src/videotext/downloader.py tests/test_downloader.py
git commit -m "feat: add yt-dlp audio adapter"
```

---

### Task 4: FFmpeg Environment Check and Audio Normalization

**Files:**
- Create: `src/videotext/audio.py`
- Create: `tests/test_audio.py`

**Interfaces:**
- Consumes: downloaded media path and a task-directory output path.
- Produces: `FfmpegAudioNormalizer.check_available() -> None`.
- Produces: `FfmpegAudioNormalizer.normalize(source: Path, destination: Path) -> Path`.

- [ ] **Step 1: Write failing subprocess-isolated tests**

```python
# tests/test_audio.py
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from videotext.audio import FfmpegAudioNormalizer
from videotext.errors import EnvironmentError, TranscriptionError


def test_check_available_calls_ffmpeg_version_without_shell():
    calls = []
    normalizer = FfmpegAudioNormalizer(runner=lambda *args, **kwargs: calls.append((args, kwargs)) or CompletedProcess(args[0], 0, "", ""))
    normalizer.check_available()
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
    assert calls[0][0] == ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(destination)]


def test_normalize_failure_preserves_source_and_has_diagnostic(tmp_path: Path):
    source = tmp_path / "input.m4a"
    source.write_bytes(b"input")
    runner = lambda command, **kwargs: CompletedProcess(command, 1, "", "invalid audio")
    with pytest.raises(TranscriptionError, match="invalid audio"):
        FfmpegAudioNormalizer(runner=runner).normalize(source, tmp_path / "normalized.wav")
    assert source.exists()
```

- [ ] **Step 2: Run audio tests and verify failure**

Run: `python -m pytest tests/test_audio.py -q`

Expected: FAIL during collection with `No module named 'videotext.audio'`.

- [ ] **Step 3: Implement FFmpeg normalization with argument arrays**

```python
# src/videotext/audio.py
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .errors import EnvironmentError, TranscriptionError


Runner = Callable[..., subprocess.CompletedProcess[str]]


class FfmpegAudioNormalizer:
    def __init__(self, runner: Runner = subprocess.run):
        self.runner = runner

    def check_available(self) -> None:
        try:
            result = self.runner(["ffmpeg", "-version"], capture_output=True, text=True, shell=False)
        except FileNotFoundError as exc:
            raise EnvironmentError("未找到 FFmpeg。", "请安装 FFmpeg 并将其加入 PATH。") from exc
        if result.returncode != 0:
            raise EnvironmentError("FFmpeg 无法正常运行。")

    def normalize(self, source: Path, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
            "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(destination),
        ]
        try:
            result = self.runner(command, capture_output=True, text=True, shell=False)
        except FileNotFoundError as exc:
            raise EnvironmentError("未找到 FFmpeg。", "请安装 FFmpeg 并将其加入 PATH。") from exc
        if result.returncode != 0 or not destination.is_file():
            detail = result.stderr.strip() or "未生成标准化音频"
            raise TranscriptionError(f"音频标准化失败：{detail}")
        return destination
```

- [ ] **Step 4: Run audio tests**

Run: `python -m pytest tests/test_audio.py -q`

Expected: all tests PASS.

- [ ] **Step 5: Lint and commit**

Run: `python -m ruff check src tests`

Expected: `All checks passed!`

```bash
git add src/videotext/audio.py tests/test_audio.py
git commit -m "feat: normalize downloaded audio with ffmpeg"
```

---

### Task 5: faster-whisper CUDA Transcriber

**Files:**
- Create: `src/videotext/transcriber.py`
- Create: `tests/test_transcriber.py`

**Interfaces:**
- Consumes: normalized audio path, `MediaMetadata`, and `TranscriptionConfig`.
- Produces: `FasterWhisperTranscriber.transcribe(audio_path: Path, metadata: MediaMetadata, config: TranscriptionConfig) -> Transcript`.

- [ ] **Step 1: Write failing transcriber tests with a fake model**

```python
# tests/test_transcriber.py
from pathlib import Path
from types import SimpleNamespace

import pytest

from videotext.errors import TranscriptionError
from videotext.models import MediaMetadata, TranscriptionConfig
from videotext.transcriber import FasterWhisperTranscriber


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
    assert created == {"name": "large-v3-turbo", "device": "cuda", "compute_type": "float16"}
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
```

- [ ] **Step 2: Run transcriber tests and verify failure**

Run: `python -m pytest tests/test_transcriber.py -q`

Expected: FAIL during collection with `No module named 'videotext.transcriber'`.

- [ ] **Step 3: Implement GPU transcription and timestamp validation**

```python
# src/videotext/transcriber.py
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from faster_whisper import WhisperModel

from .errors import TranscriptionError
from .models import MediaMetadata, Transcript, TranscriptSegment, TranscriptionConfig


ModelFactory = Callable[..., Any]


class FasterWhisperTranscriber:
    def __init__(self, model_factory: ModelFactory = WhisperModel):
        self.model_factory = model_factory

    def transcribe(
        self,
        audio_path: Path,
        metadata: MediaMetadata,
        config: TranscriptionConfig,
    ) -> Transcript:
        try:
            model = self.model_factory(
                config.model,
                device=config.device,
                compute_type=config.compute_type,
            )
            segments_iter, info = model.transcribe(
                str(audio_path),
                language=config.language,
                vad_filter=config.vad_filter,
            )
            segments = tuple(
                TranscriptSegment(float(item.start), float(item.end), item.text.strip())
                for item in segments_iter
                if item.text.strip()
            )
        except Exception as exc:
            message = str(exc)
            hint = "请确认 CUDA 运行库兼容；显存不足时使用 --model medium 或较低计算精度。"
            raise TranscriptionError(f"GPU 转写失败：{message}", hint) from exc
        if not segments:
            raise TranscriptionError("音频中没有检测到有效语音。")
        previous_end = 0.0
        for segment in segments:
            if segment.start < 0 or segment.end < segment.start or segment.start < previous_end:
                raise TranscriptionError("识别结果包含非法或非单调时间戳。")
            previous_end = segment.end
        return Transcript(
            source_url=metadata.source_url,
            platform=metadata.platform,
            video_id=metadata.video_id,
            title=metadata.title,
            duration=float(metadata.duration or 0.0),
            detected_language=str(info.language),
            language_probability=float(info.language_probability),
            model=config.model,
            created_at=datetime.now(timezone.utc).isoformat(),
            segments=segments,
        )
```

- [ ] **Step 4: Run transcriber tests**

Run: `python -m pytest tests/test_transcriber.py -q`

Expected: all tests PASS.

- [ ] **Step 5: Lint and commit**

Run: `python -m ruff check src tests`

Expected: `All checks passed!`

```bash
git add src/videotext/transcriber.py tests/test_transcriber.py
git commit -m "feat: transcribe normalized audio on cuda"
```

---

### Task 6: Application Service, Cleanup Policy, and Progress Events

**Files:**
- Create: `src/videotext/service.py`
- Create: `tests/test_service.py`

**Interfaces:**
- Consumes: Task 1–5 adapters and models.
- Produces: `ProgressEvent(stage: str, message: str)`.
- Produces: `RunOptions(url: str, output_root: Path, config: TranscriptionConfig, keep_audio: bool, overwrite: bool)`.
- Produces: `VideoTextService.run(options: RunOptions, on_progress: Callable[[ProgressEvent], None]) -> tuple[Path, ...]`.

- [ ] **Step 1: Write failing end-to-end orchestration tests using fakes**

```python
# tests/test_service.py
from pathlib import Path

import pytest

from videotext.errors import TranscriptionError
from videotext.models import MediaMetadata, MediaSource, Transcript, TranscriptSegment, TranscriptionConfig
from videotext.service import RunOptions, VideoTextService


META = MediaMetadata("https://example.com/1", "Example", "1", "Demo", 10.0, False, False)
TRANSCRIPT = Transcript("https://example.com/1", "Example", "1", "Demo", 10.0, "zh", 0.9, "large-v3-turbo", "2026-07-13T00:00:00+00:00", (TranscriptSegment(0, 1, "你好"),))


class FakeDownloader:
    def probe(self, url):
        return META

    def download(self, url, target):
        path = target / "downloaded.m4a"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"audio")
        return MediaSource(META, path, {"id": "1"})


class FakeAudio:
    def check_available(self):
        return None

    def normalize(self, source, destination):
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
    return RunOptions("https://example.com/1", tmp_path / "outputs", TranscriptionConfig(), keep_audio, False)


def test_success_runs_stages_and_removes_temp_directory(tmp_path: Path):
    events = []
    service = VideoTextService(FakeDownloader(), FakeAudio(), FakeTranscriber(), fake_export, tmp_path / "temp", "0.1.0")
    paths = service.run(options(tmp_path), events.append)
    assert paths[0].read_text(encoding="utf-8") == "你好\n"
    assert [event.stage for event in events] == ["probe", "download", "normalize", "transcribe", "export", "done"]
    assert not (tmp_path / "temp").exists()


def test_keep_audio_copies_normalized_audio_to_output(tmp_path: Path):
    service = VideoTextService(FakeDownloader(), FakeAudio(), FakeTranscriber(), fake_export, tmp_path / "temp", "0.1.0")
    service.run(options(tmp_path, keep_audio=True), lambda event: None)
    assert (tmp_path / "outputs" / "Demo_1" / "audio.wav").is_file()


def test_failure_preserves_task_temp_and_writes_no_final_output(tmp_path: Path):
    error = TranscriptionError("boom")
    service = VideoTextService(FakeDownloader(), FakeAudio(), FakeTranscriber(error), fake_export, tmp_path / "temp", "0.1.0")
    with pytest.raises(TranscriptionError):
        service.run(options(tmp_path), lambda event: None)
    assert list((tmp_path / "temp").glob("*/normalized.wav"))
    assert not (tmp_path / "outputs" / "Demo_1").exists()
```

- [ ] **Step 2: Run service tests and verify failure**

Run: `python -m pytest tests/test_service.py -q`

Expected: FAIL during collection with `No module named 'videotext.service'`.

- [ ] **Step 3: Implement orchestration and cleanup rules**

```python
# src/videotext/service.py
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

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
    def transcribe(self, audio_path: Path, metadata: MediaMetadata, config: TranscriptionConfig) -> Transcript: ...


Exporter = Callable[[Transcript, MediaSource, Path, bool, str, Path | None], tuple[Path, ...]]
ProgressCallback = Callable[[ProgressEvent], None]


class VideoTextService:
    def __init__(self, downloader: Downloader, audio: AudioNormalizer, transcriber: Transcriber, exporter: Exporter, temp_root: Path, tool_version: str):
        self.downloader = downloader
        self.audio = audio
        self.transcriber = transcriber
        self.exporter = exporter
        self.temp_root = temp_root
        self.tool_version = tool_version

    def run(self, options: RunOptions, on_progress: ProgressCallback) -> tuple[Path, ...]:
        url = validate_url(options.url)
        on_progress(ProgressEvent("probe", "正在读取媒体信息"))
        metadata = self.downloader.probe(url)
        validate_media(metadata)
        final_dir = output_directory(options.output_root, metadata)
        self.audio.check_available()
        self.temp_root.mkdir(parents=True, exist_ok=True)
        task_dir = Path(tempfile.mkdtemp(prefix="videotext-", dir=self.temp_root))
        succeeded = False
        try:
            on_progress(ProgressEvent("download", "正在下载音频"))
            source = self.downloader.download(url, task_dir)
            validate_media(source.metadata)
            on_progress(ProgressEvent("normalize", "正在标准化音频"))
            normalized = self.audio.normalize(source.downloaded_audio_path, task_dir / "normalized.wav")
            on_progress(ProgressEvent("transcribe", "正在使用 GPU 转写"))
            transcript = self.transcriber.transcribe(normalized, metadata, options.config)
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
```

- [ ] **Step 4: Run orchestration and all prior tests**

Run: `python -m pytest tests/test_service.py tests/test_exporters.py tests/test_downloader.py tests/test_audio.py tests/test_transcriber.py -q`

Expected: all tests PASS.

- [ ] **Step 5: Lint and commit**

Run: `python -m ruff check src tests`

Expected: `All checks passed!`

```bash
git add src/videotext/service.py tests/test_service.py
git commit -m "feat: orchestrate video transcription pipeline"
```

---

### Task 7: Typer CLI, Diagnostics, README, and Opt-in Smoke Test

**Files:**
- Create: `src/videotext/cli.py`
- Create: `tests/test_cli.py`
- Create: `tests/test_smoke_local.py`
- Create: `README.md`

**Interfaces:**
- Consumes: `VideoTextService`, all concrete adapters, typed errors, and package version.
- Produces: installed `videotext` command.
- Produces: `_build_service() -> VideoTextService` and Typer `app` for CLI testing.

- [ ] **Step 1: Write failing CLI tests**

```python
# tests/test_cli.py
from pathlib import Path

from typer.testing import CliRunner

from videotext.cli import app
from videotext.errors import InputError
from videotext.service import ProgressEvent


runner = CliRunner()


class SuccessfulService:
    def run(self, options, on_progress):
        on_progress(ProgressEvent("done", "处理完成"))
        return (options.output_root / "Demo_1" / "transcript.txt",)


class FailingService:
    def run(self, options, on_progress):
        raise InputError("第一版不支持播放列表。", "请提供单个视频链接。")


def test_cli_success_prints_result_and_uses_defaults(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("videotext.cli._build_service", lambda: SuccessfulService())
    result = runner.invoke(app, ["https://example.com/1", "--output", str(tmp_path)])
    assert result.exit_code == 0
    assert "处理完成" in result.stdout
    assert "transcript.txt" in result.stdout


def test_cli_domain_error_prints_hint_and_uses_typed_exit_code(monkeypatch):
    monkeypatch.setattr("videotext.cli._build_service", lambda: FailingService())
    result = runner.invoke(app, ["https://example.com/list"])
    assert result.exit_code == 2
    assert "播放列表" in result.stdout
    assert "单个视频链接" in result.stdout
    assert "Traceback" not in result.stdout


def test_cli_verbose_exposes_unexpected_exception(monkeypatch):
    class Broken:
        def run(self, options, on_progress):
            raise RuntimeError("unexpected")
    monkeypatch.setattr("videotext.cli._build_service", lambda: Broken())
    result = runner.invoke(app, ["https://example.com/1", "--verbose"])
    assert result.exit_code == 1
    assert "RuntimeError" in result.stdout
```

- [ ] **Step 2: Run CLI tests and verify failure**

Run: `python -m pytest tests/test_cli.py -q`

Expected: FAIL during collection with `No module named 'videotext.cli'`.

- [ ] **Step 3: Implement CLI composition and error presentation**

```python
# src/videotext/cli.py
import tempfile
import traceback
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from . import __version__
from .audio import FfmpegAudioNormalizer
from .downloader import YtDlpDownloader
from .errors import VideoTextError
from .exporters import export_transcript
from .models import TranscriptionConfig
from .service import ProgressEvent, RunOptions, VideoTextService
from .transcriber import FasterWhisperTranscriber


app = typer.Typer(add_completion=False, no_args_is_help=True)
console = Console()


def _build_service() -> VideoTextService:
    return VideoTextService(
        downloader=YtDlpDownloader(),
        audio=FfmpegAudioNormalizer(),
        transcriber=FasterWhisperTranscriber(),
        exporter=export_transcript,
        temp_root=Path(tempfile.gettempdir()) / "videotext",
        tool_version=__version__,
    )


@app.command()
def main(
    url: Annotated[str, typer.Argument(help="一个公开单视频链接")],
    output: Annotated[Path, typer.Option("--output", "-o")] = Path("outputs"),
    model: Annotated[str, typer.Option("--model")] = "large-v3-turbo",
    language: Annotated[str | None, typer.Option("--language")] = None,
    keep_audio: Annotated[bool, typer.Option("--keep-audio")] = False,
    overwrite: Annotated[bool, typer.Option("--overwrite")] = False,
    verbose: Annotated[bool, typer.Option("--verbose")] = False,
) -> None:
    started = __import__("time").perf_counter()
    options = RunOptions(
        url=url,
        output_root=output,
        config=TranscriptionConfig(model=model, language=language),
        keep_audio=keep_audio,
        overwrite=overwrite,
    )

    def progress(event: ProgressEvent) -> None:
        console.print(f"[cyan]{event.stage}[/cyan] {event.message}")

    try:
        paths = _build_service().run(options, progress)
    except VideoTextError as exc:
        console.print(f"[red]失败：{exc.message}[/red]")
        console.print(f"建议：{exc.hint}")
        if verbose:
            console.print(traceback.format_exc())
        raise typer.Exit(exc.exit_code) from exc
    except Exception as exc:
        console.print(f"[red]未预期错误：{exc}[/red]")
        if verbose:
            console.print(traceback.format_exc())
        raise typer.Exit(1) from exc

    elapsed = __import__("time").perf_counter() - started
    console.print(f"[green]处理完成，用时 {elapsed:.1f} 秒[/green]")
    for path in paths:
        console.print(str(path.resolve()))
```

- [ ] **Step 4: Add the explicitly opt-in external smoke test**

```python
# tests/test_smoke_local.py
import os
from pathlib import Path

import pytest

from videotext.audio import FfmpegAudioNormalizer
from videotext.downloader import YtDlpDownloader
from videotext.exporters import export_transcript
from videotext.models import TranscriptionConfig
from videotext.service import RunOptions, VideoTextService
from videotext.transcriber import FasterWhisperTranscriber


@pytest.mark.skipif(not os.getenv("VIDEOTEXT_SMOKE_URL"), reason="set VIDEOTEXT_SMOKE_URL")
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
    assert {path.name for path in paths} == {"transcript.txt", "transcript.srt", "transcript.json", "source.json"}
    assert all(path.stat().st_size > 0 for path in paths)
```

- [ ] **Step 5: Add exact installation and usage documentation**

```markdown
# README.md
# videotext

将一个公开的 Bilibili、抖音或 YouTube 单视频链接在本地转换为 TXT、SRT 和 JSON。音频通过 yt-dlp 获取，识别通过 NVIDIA GPU 和 faster-whisper 完成。

## 前提

- Windows 10/11
- Python 3.11 或更新版本
- NVIDIA GPU 和与当前 CTranslate2 版本兼容的 CUDA 运行库
- FFmpeg 已加入 `PATH`
- 仅处理你拥有或获准使用的公开内容

## 安装

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
ffmpeg -version
videotext --help
```

首次转写会下载所选 Whisper 模型，需要额外等待并占用磁盘空间。

## 使用

```powershell
videotext "https://www.bilibili.com/video/BV..."
videotext "https://www.youtube.com/watch?v=..." --output .\outputs
videotext "https://www.douyin.com/video/..." --language zh
videotext URL --model large-v3 --keep-audio
```

默认拒绝播放列表、直播、登录内容、未知时长来源和超过 2 小时的视频。平台页面可能变化；解析失败时先升级 yt-dlp：

```powershell
python -m pip install --upgrade yt-dlp
```

## 验证

```powershell
python -m pytest -q
python -m ruff check src tests
$env:VIDEOTEXT_SMOKE_URL=$env:AUTHORIZED_PUBLIC_VIDEO_URL
python -m pytest tests/test_smoke_local.py -q
```

普通测试不会访问网络、调用 FFmpeg 或加载 GPU 模型。冒烟测试只在显式设置 `VIDEOTEXT_SMOKE_URL` 时运行。
```

- [ ] **Step 6: Run CLI tests, full unit suite, and lint**

Run: `python -m pytest tests/test_cli.py -q`

Expected: all CLI tests PASS.

Run: `python -m pytest -q`

Expected: all ordinary tests PASS and `tests/test_smoke_local.py` reports one SKIPPED test when `VIDEOTEXT_SMOKE_URL` is unset.

Run: `python -m ruff check src tests`

Expected: `All checks passed!`

Run: `videotext --help`

Expected: exit code 0 and help lists `--output`, `--model`, `--language`, `--keep-audio`, `--overwrite`, and `--verbose`.

- [ ] **Step 7: Run authorized platform smoke tests one at a time**

For each short public video that the tester owns or is authorized to process:

Before running the commands, define `AUTHORIZED_BILIBILI_URL`, `AUTHORIZED_DOUYIN_URL`, and `AUTHORIZED_YOUTUBE_URL` in the user's environment with actual authorized public-video URLs. Do not store those values in the repository.

```powershell
$env:VIDEOTEXT_SMOKE_URL=$env:AUTHORIZED_BILIBILI_URL
python -m pytest tests/test_smoke_local.py -q
$env:VIDEOTEXT_SMOKE_URL=$env:AUTHORIZED_DOUYIN_URL
python -m pytest tests/test_smoke_local.py -q
$env:VIDEOTEXT_SMOKE_URL=$env:AUTHORIZED_YOUTUBE_URL
python -m pytest tests/test_smoke_local.py -q
Remove-Item Env:VIDEOTEXT_SMOKE_URL
```

Expected for each available platform URL: one PASS, four non-empty output files, legal monotonic SRT timestamps, and no retained task temp directory after success. A platform-side extractor failure must be recorded as a compatibility result, not hidden by changing authentication or access controls.

- [ ] **Step 8: Commit the completed CLI**

```bash
git add src/videotext/cli.py tests/test_cli.py tests/test_smoke_local.py README.md
git commit -m "feat: expose local video transcription cli"
```

---

## Final Verification Checklist

- [ ] Run `python -m pytest -q`; expect all offline tests PASS and the opt-in smoke test SKIPPED when its environment variable is absent.
- [ ] Run `python -m ruff check src tests`; expect `All checks passed!`.
- [ ] Run `videotext --help`; expect all documented options.
- [ ] Run `ffmpeg -version`; expect exit code 0.
- [ ] Run `nvidia-smi`; expect the intended NVIDIA GPU to be visible.
- [ ] Run one authorized short-video smoke test per target platform where current yt-dlp support is available.
- [ ] Inspect `transcript.txt`, `transcript.srt`, `transcript.json`, and `source.json` for UTF-8 text consistency and absence of secrets.
- [ ] Force a transcription failure and confirm the task audio remains while no final transcript files appear.
- [ ] Force an existing-output collision and confirm the command exits with code 7 unless `--overwrite` is set.
- [ ] Confirm no code path passes the URL to `subprocess` or uses `shell=True`.
