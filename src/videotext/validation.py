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


def validate_media(
    metadata: MediaMetadata,
    max_duration: float = MAX_VIDEO_DURATION_SECONDS,
) -> None:
    if metadata.is_playlist:
        raise InputError("第一版不支持播放列表。")
    if metadata.is_live:
        raise InputError("第一版不支持直播。")
    if metadata.duration is None:
        raise InputError("无法确认视频时长，因此拒绝处理。")
    if metadata.duration > max_duration:
        raise InputError("视频超过 3 小时时长限制。")
