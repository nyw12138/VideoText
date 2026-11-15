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
        (metadata(duration=10800.01), "3 小时"),
    ],
)
def test_validate_media_rejects_out_of_scope_sources(item, message):
    with pytest.raises(InputError, match=message):
        validate_media(item)


@pytest.mark.parametrize("duration", [0.0, 10799.0, 10800.0])
def test_validate_media_accepts_duration_boundary(duration):
    validate_media(metadata(duration=duration))
