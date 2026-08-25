import pytest

from pocket48_summarizer.errors import ConfigurationError, ExternalServiceError
from pocket48_summarizer.models import MemberCatalogEntry
from pocket48_summarizer.vocabulary import VocabularyManager


class FakeDashScopeVocabulary:
    def __init__(self):
        self.created = []
        self.deleted = []
        self.fail_create = False

    async def create_vocabulary(
        self, *, prefix, target_model, vocabulary
    ):
        if self.fail_create:
            raise ExternalServiceError(
                "dashscope_vocabulary_create_failed",
                "热词服务暂时不可用",
                True,
            )
        vocabulary_id = f"vocab-{len(self.created) + 1}"
        self.created.append(
            {
                "id": vocabulary_id,
                "prefix": prefix,
                "target_model": target_model,
                "vocabulary": vocabulary,
            }
        )
        return vocabulary_id

    async def query_vocabulary(self, vocabulary_id):
        return {
            "status": "OK",
            "target_model": "paraformer-v2",
            "vocabulary_id": vocabulary_id,
        }

    async def delete_vocabulary(self, vocabulary_id):
        self.deleted.append(vocabulary_id)


def seed_catalog(repository):
    repository.replace_member_catalog(
        [
            MemberCatalogEntry(
                member_id="10324",
                canonical_name="蒋夏羽",
                group_id="10",
                group_name="SNH",
                team_id="101",
                team_name="SII",
                status="99",
                active=True,
            ),
            MemberCatalogEntry(
                member_id="10337",
                canonical_name="曹可甜",
                group_id="10",
                group_name="SNH",
                team_id="101",
                team_name="SII",
                status="99",
                active=True,
            ),
        ],
        source_url="https://h5.48.cn/catalog",
        source_hash="a" * 64,
    )
    repository.create_glossary_alias(
        alias="甜甜",
        member_id="10337",
        user_id="local",
    )


@pytest.mark.asyncio
async def test_vocabulary_builds_reuses_and_replaces(settings, repository):
    seed_catalog(repository)
    repository.create_glossary_term(
        canonical_text="春晚",
        term_type="event",
        description_zh="年度活动",
        description_en="Annual event",
        user_id="local",
    )
    dashscope = FakeDashScopeVocabulary()
    manager = VocabularyManager(settings, repository, dashscope)

    first = await manager.ensure_current()
    second = await manager.ensure_current()

    assert first and second
    assert first.vocabulary_id == "vocab-1"
    assert second == first
    assert len(dashscope.created) == 1
    texts = {
        entry["text"]
        for entry in dashscope.created[0]["vocabulary"]
    }
    assert {"曹可甜", "蒋夏羽", "甜甜", "春晚", "SNH", "SII"} <= texts

    repository.create_glossary_term(
        canonical_text="梦想的旗帜",
        term_type="stage",
        description_zh="公演名称",
        description_en="Stage title",
        user_id="local",
    )
    replaced = await manager.ensure_current()

    assert replaced and replaced.vocabulary_id == "vocab-2"
    assert dashscope.deleted == ["vocab-1"]
    state = repository.get_glossary_sync_state()
    assert state.active_vocabulary_id == "vocab-2"
    assert state.vocabulary_fingerprint == replaced.fingerprint
    assert state.vocabulary_error is None


@pytest.mark.asyncio
async def test_vocabulary_rebuild_failure_retains_previous(
    settings, repository
):
    seed_catalog(repository)
    dashscope = FakeDashScopeVocabulary()
    manager = VocabularyManager(settings, repository, dashscope)
    active = await manager.ensure_current()
    repository.create_glossary_term(
        canonical_text="新的术语",
        term_type="other",
        description_zh="",
        description_en="",
        user_id="local",
    )
    dashscope.fail_create = True

    retained = await manager.ensure_current()
    retained_again = await manager.ensure_current()

    assert active and retained
    assert retained.vocabulary_id == active.vocabulary_id
    assert retained_again == retained
    assert len(dashscope.created) == 1
    state = repository.get_glossary_sync_state()
    assert state.active_vocabulary_id == active.vocabulary_id
    assert state.vocabulary_error == "热词服务暂时不可用"


@pytest.mark.asyncio
async def test_vocabulary_rejects_unsupported_model(settings, repository):
    seed_catalog(repository)
    manager = VocabularyManager(
        settings.model_copy(update={"dashscope_asr_model": "paraformer-v1"}),
        repository,
        FakeDashScopeVocabulary(),
    )

    with pytest.raises(ConfigurationError, match="paraformer-v2"):
        await manager.ensure_current()


@pytest.mark.asyncio
async def test_vocabulary_obeys_term_count_and_text_limits(
    settings, repository
):
    repository.replace_member_catalog(
        [
            MemberCatalogEntry(
                member_id="1",
                canonical_name="有效名字",
                status="99",
                active=True,
            ),
            MemberCatalogEntry(
                member_id="2",
                canonical_name="这是一个超过十五个字符而不能提交的成员规范名称",
                status="99",
                active=True,
            ),
        ],
        source_url="https://h5.48.cn/catalog",
        source_hash="b" * 64,
    )
    dashscope = FakeDashScopeVocabulary()
    manager = VocabularyManager(
        settings.model_copy(
            update={"dashscope_vocabulary_max_terms": 1}
        ),
        repository,
        dashscope,
    )

    await manager.ensure_current()

    assert dashscope.created[0]["vocabulary"] == [
        {"text": "有效名字", "weight": 4, "lang": "zh"}
    ]
