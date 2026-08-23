import pytest

from pocket48_summarizer.errors import AppError
from pocket48_summarizer.security import parse_share_url, validate_https_url


def test_parse_supported_share_url():
    normalized, live_id = parse_share_url(
        " https://h5.48.cn/2019appshare/memberLiveShare/index.html"
        "?id=1297967327104274432 "
    )
    assert live_id == "1297967327104274432"
    assert normalized.endswith(f"?id={live_id}")


@pytest.mark.parametrize(
    "url",
    [
        "http://h5.48.cn/2019appshare/memberLiveShare/index.html?id=123456",
        "https://h5.48.cn.evil.test/2019appshare/memberLiveShare/index.html?id=123456",
        "https://user@h5.48.cn/2019appshare/memberLiveShare/index.html?id=123456",
        "https://h5.48.cn/other?id=123456",
        "https://h5.48.cn/2019appshare/memberLiveShare/index.html?id=abc",
    ],
)
def test_rejects_unsupported_share_urls(url):
    with pytest.raises(AppError):
        parse_share_url(url)


def test_remote_url_host_is_exact():
    assert (
        validate_https_url(
            "https://idol-vod.48.cn/path/replay.m3u8",
            {"idol-vod.48.cn"},
            code="bad",
            label="媒体",
        )
        == "https://idol-vod.48.cn/path/replay.m3u8"
    )
    with pytest.raises(AppError):
        validate_https_url(
            "https://idol-vod.48.cn.evil.test/path",
            {"idol-vod.48.cn"},
            code="bad",
            label="媒体",
        )
