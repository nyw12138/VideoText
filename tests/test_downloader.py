from pathlib import Path

import pytest
from yt_dlp.utils import DownloadError as YtDownloadError

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

    def sanitize_info(self, info):
        return {**info, "sanitized": True}


def info(**overrides):
    value = {
        "id": "abc",
        "title": "Demo",
        "duration": 60.0,
        "extractor_key": "Youtube",
        "webpage_url": "https://example.com/abc",
        "_type": "video",
        "is_live": False,
        "ext": "m4a",
    }
    value.update(overrides)
    return value


def test_probe_maps_metadata_without_downloading():
    holder = {}

    def factory(options):
        holder["ydl"] = FakeYDL(options, info())
        return holder["ydl"]

    result = YtDlpDownloader(ydl_factory=factory).probe("https://example.com/abc")
    assert result.video_id == "abc"
    assert result.platform == "Youtube"
    assert holder["ydl"].options["skip_download"] is True


def test_probe_marks_playlist():
    downloader = YtDlpDownloader(
        ydl_factory=lambda options: FakeYDL(options, info(_type="playlist"))
    )
    assert downloader.probe("https://example.com/list").is_playlist is True


def test_download_selects_audio_only_and_returns_existing_path(tmp_path: Path):
    holder = {}

    def factory(options):
        audio = tmp_path / "downloaded.m4a"
        audio.write_bytes(b"audio")
        holder["ydl"] = FakeYDL(options, info())
        return holder["ydl"]

    source = YtDlpDownloader(ydl_factory=factory).download("https://example.com/abc", tmp_path)
    assert source.downloaded_audio_path == tmp_path / "downloaded.m4a"
    assert source.raw_metadata["id"] == "abc"
    assert source.raw_metadata["sanitized"] is True
    assert holder["ydl"].options["noprogress"] is True


def test_expected_extractor_error_becomes_platform_error():
    downloader = YtDlpDownloader(
        ydl_factory=lambda options: FakeYDL(
            options, error=YtDownloadError("login required")
        )
    )
    with pytest.raises(PlatformError, match="解析"):
        downloader.probe("https://example.com/private")


def test_missing_downloaded_file_becomes_download_error(tmp_path: Path):
    downloader = YtDlpDownloader(ydl_factory=lambda options: FakeYDL(options, info()))
    with pytest.raises(DownloadError, match="音频文件"):
        downloader.download("https://example.com/abc", tmp_path)
