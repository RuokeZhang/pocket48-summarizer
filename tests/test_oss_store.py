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
