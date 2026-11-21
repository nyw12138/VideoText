from pathlib import Path
from urllib.parse import urlparse

from .errors import InputError
from .models import MediaMetadata

MAX_VIDEO_DURATION_SECONDS = 3 * 60 * 60


def validate_url(url: str) -> str:
    value = url.strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise InputError("链接必须是有效的 HTTP 或 HTTPS URL。")
    return value


def validate_local_file(file_path: Path) -> Path:
    candidate = file_path.expanduser()
    if not candidate.exists():
        raise InputError("找不到所选文件。", "请重新选择一个本地媒体文件。")
    if not candidate.is_file():
        raise InputError("所选路径不是文件。", "请选择一个音频或视频文件。")
    try:
        resolved = candidate.resolve(strict=True)
        if resolved.stat().st_size == 0:
            raise InputError("所选文件为空。", "请选择包含有效媒体内容的文件。")
    except OSError as exc:
        raise InputError(f"无法读取所选文件：{exc}") from exc
    return resolved


def validate_media(
    metadata: MediaMetadata,
    max_duration: float = MAX_VIDEO_DURATION_SECONDS,
) -> None:
    if metadata.is_playlist:
        raise InputError("第一版不支持播放列表。")
    if metadata.is_live:
        raise InputError("第一版不支持直播。")
    if metadata.duration is None:
        raise InputError("无法确认媒体时长，因此拒绝处理。")
    if metadata.duration > max_duration:
        raise InputError("媒体超过 3 小时时长限制。")
