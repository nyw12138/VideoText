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
        options = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": False,
        }
        try:
            with self.ydl_factory(options) as ydl:
                info = ydl.extract_info(url, download=False)
            if not isinstance(info, dict):
                raise PlatformError("平台没有返回可用的媒体信息。")
            return self._metadata(url, info)
        except PlatformError:
            raise
        except YtDownloadError as exc:
            raise PlatformError(f"平台解析失败：{exc}") from exc
        except Exception as exc:
            raise PlatformError(f"读取媒体信息失败：{exc}") from exc

    def download(self, url: str, target_dir: Path) -> MediaSource:
        target_dir.mkdir(parents=True, exist_ok=True)
        options = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "noplaylist": True,
            "format": "bestaudio/best",
            "paths": {"home": str(target_dir)},
            "outtmpl": {"default": "downloaded.%(ext)s"},
            "restrictfilenames": False,
        }
        try:
            with self.ydl_factory(options) as ydl:
                info = ydl.extract_info(url, download=True)
                if not isinstance(info, dict):
                    raise DownloadError("下载器没有返回媒体信息。")
                path = Path(ydl.prepare_filename(info))
                sanitize = getattr(ydl, "sanitize_info", lambda value: value)
                raw_metadata = sanitize(info)
            if not path.is_file():
                raise DownloadError("下载器未生成预期的音频文件。")
            return MediaSource(self._metadata(url, info), path, raw_metadata)
        except DownloadError:
            raise
        except YtDownloadError as exc:
            raise DownloadError(f"音频下载失败：{exc}") from exc
        except Exception as exc:
            raise DownloadError(f"音频下载失败：{exc}") from exc
