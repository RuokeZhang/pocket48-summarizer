import pytest

from pocket48_summarizer.clients.oss_store import OSSStore


@pytest.mark.asyncio
async def test_signing_uses_public_endpoint(settings, monkeypatch):
    buckets = []

    class FakeBucket:
        def __init__(self, auth, endpoint, bucket):
            self.endpoint = endpoint
            buckets.append(self)

        def sign_url(self, method, key, expires, slash_safe):
            return f"{self.endpoint}/{key}"

    monkeypatch.setattr(
        "pocket48_summarizer.clients.oss_store.oss2.Bucket", FakeBucket
    )
    public_settings = settings.model_copy(
        update={
            "aliyun_oss_endpoint": (
                "https://oss-cn-beijing-internal.aliyuncs.com"
            ),
            "aliyun_oss_public_endpoint": (
                "https://oss-cn-beijing.aliyuncs.com"
            ),
        }
    )

    store = OSSStore(public_settings)
    signed_url = await store.signed_get_url("temporary/job/audio.mp3")

    assert len(buckets) == 2
    assert buckets[0].endpoint.endswith("-internal.aliyuncs.com")
    assert buckets[1].endpoint == "https://oss-cn-beijing.aliyuncs.com"
    assert signed_url.startswith("https://oss-cn-beijing.aliyuncs.com/")


@pytest.mark.asyncio
async def test_clip_upload_uses_permanent_prefix_and_video_headers(
    settings, monkeypatch, tmp_path
):
    uploads = []
    signatures = []

    class FakeBucket:
        def __init__(self, auth, endpoint, bucket):
            self.endpoint = endpoint

        def put_object_from_file(self, key, path, headers):
            uploads.append((key, path, headers))

        def sign_url(self, method, key, expires, slash_safe):
            signatures.append((method, key, expires, slash_safe))
            return f"https://download.example/{key}"

    monkeypatch.setattr(
        "pocket48_summarizer.clients.oss_store.oss2.Bucket", FakeBucket
    )
    clip_settings = settings.model_copy(
        update={
            "aliyun_oss_clip_prefix": "permanent-clips",
            "aliyun_oss_clip_signed_url_seconds": 1800,
        }
    )
    store = OSSStore(clip_settings)
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"video")
    key = store.clip_object_key("job-1", path.name)

    await store.upload_clip(path, key, path.name)
    signed_url = await store.signed_clip_url(key)

    assert key == "permanent-clips/job-1/clip.mp4"
    assert uploads[0][2]["Content-Type"] == "video/mp4"
    assert uploads[0][2]["Content-Disposition"].endswith(
        'filename="clip.mp4"'
    )
    assert signatures == [("GET", key, 1800, True)]
    assert signed_url == f"https://download.example/{key}"
