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
        return {
            key: _clean(item)
            for key, item in value.items()
            if key.lower() not in SENSITIVE_KEYS
        }
    if isinstance(value, list):
        return [_clean(item) for item in value]
    return value


def _payloads(transcript: Transcript, source: MediaSource, tool_version: str) -> dict[str, str]:
    text = "".join(
        f"{segment.text.strip()}\n" for segment in transcript.segments if segment.text.strip()
    )
    blocks = []
    for index, segment in enumerate(transcript.segments, 1):
        blocks.append(
            f"{index}\n{format_srt_timestamp(segment.start)} --> "
            f"{format_srt_timestamp(segment.end)}\n{segment.text.strip()}\n"
        )
    transcript_json = json.dumps(transcript.to_dict(), ensure_ascii=False, indent=2) + "\n"
    source_json = (
        json.dumps(
            {"tool_version": tool_version, "metadata": _clean(source.raw_metadata)},
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
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
        for name, content in _payloads(transcript, source, tool_version).items():
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
