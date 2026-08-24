import json

import httpx
import pytest

from pocket48_summarizer.clients.member_catalog import MemberCatalogClient
from pocket48_summarizer.errors import AppError
from pocket48_summarizer.glossary import MemberCatalogService
from pocket48_summarizer.models import MemberCatalogEntry


def catalog_payload(rows):
    return {"total": str(len(rows)), "rows": rows}


def member_row(
    sid="10337",
    *,
    name="曹可甜",
    status="99",
    team_id="101",
    team_name="SII",
):
    return {
        "sid": sid,
        "status": status,
        "gid": "10",
        "tid": team_id,
        "sname": name,
        "ranking": "0",
        "pinyin": "Cao KeTian",
        "gname": "SNH",
        "tname": team_name,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("jsonp", [False, True])
async def test_member_catalog_client_parses_json_and_jsonp(settings, jsonp):
    payload = catalog_payload(
        [
            member_row(),
            {
                **member_row(
                    "10324",
                    name="蒋夏羽",
                    status="44",
                    team_id="44",
                    team_name="暂休",
                ),
                "ranking": None,
            },
        ]
    )
    body = json.dumps(payload, ensure_ascii=False)
    if jsonp:
        body = f"official.members({body});"
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, text=body)
        )
    )
    catalog = MemberCatalogClient(settings, client)

    members = await catalog.fetch_members()

    assert [member.member_id for member in members] == ["10337", "10324"]
    assert members[0].active is True
    assert members[1].active is False
    assert members[1].ranking == 0
    await client.aclose()


@pytest.mark.asyncio
async def test_member_catalog_client_rejects_invalid_wrapper(settings):
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                text='alert(1); {"total":"0","rows":[]}',
            )
        )
    )
    catalog = MemberCatalogClient(settings, client)

    with pytest.raises(AppError) as exc_info:
        await catalog.fetch_members()

    assert exc_info.value.code == "member_catalog_jsonp_invalid"
    await client.aclose()


@pytest.mark.asyncio
async def test_member_catalog_client_bounds_response_size(settings):
    small_settings = settings.model_copy(
        update={"member_catalog_max_response_bytes": 128}
    )
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, content=b"x" * 129)
        )
    )
    catalog = MemberCatalogClient(small_settings, client)

    with pytest.raises(AppError) as exc_info:
        await catalog.fetch_members()

    assert exc_info.value.code == "member_catalog_response_too_large"
    await client.aclose()


def test_member_catalog_upsert_preserves_aliases_and_marks_absent_inactive(
    repository,
):
    initial = [
        MemberCatalogEntry(
            member_id="10337",
            canonical_name="曹可甜",
            pinyin="Cao KeTian",
            group_id="10",
            group_name="SNH",
            team_id="101",
            team_name="SII",
            status="99",
            active=True,
        ),
        MemberCatalogEntry(
            member_id="10324",
            canonical_name="蒋夏羽",
            pinyin="Jiang XiaYu",
            group_id="10",
            group_name="SNH",
            team_id="101",
            team_name="SII",
            status="99",
            active=True,
        ),
    ]
    first = repository.replace_member_catalog(
        initial,
        source_url="https://h5.48.cn/catalog",
        source_hash="a" * 64,
    )
    alias = repository.create_glossary_alias(
        alias="甜甜",
        member_id="10337",
        user_id="local",
    )
    aliased_fingerprint = repository.get_glossary_sync_state().glossary_fingerprint

    second = repository.replace_member_catalog(
        [
            initial[0].model_copy(
                update={"canonical_name": "曹可甜（官方）"}
            )
        ],
        source_url="https://h5.48.cn/catalog",
        source_hash="b" * 64,
    )

    retained_alias = repository.list_glossary_aliases()[0]
    absent = repository.get_member_catalog("10324")
    assert first.member_count == 2
    assert second.member_count == 1
    assert second.catalog_version == "b" * 16
    assert alias.id == retained_alias.id
    assert retained_alias.target_text == "曹可甜（官方）"
    assert absent and absent.source_present is False
    assert absent.active is False
    assert second.glossary_fingerprint != aliased_fingerprint


def test_glossary_aliases_are_globally_unambiguous(repository):
    repository.replace_member_catalog(
        [
            MemberCatalogEntry(
                member_id="10337",
                canonical_name="曹可甜",
                status="99",
                active=True,
            ),
            MemberCatalogEntry(
                member_id="10324",
                canonical_name="蒋夏羽",
                status="99",
                active=True,
            ),
        ],
        source_url="https://h5.48.cn/catalog",
        source_hash="c" * 64,
    )
    repository.create_glossary_alias(
        alias="甜甜",
        member_id="10337",
        user_id="local",
    )

    with pytest.raises(AppError) as exc_info:
        repository.create_glossary_alias(
            alias=" 甜甜 ",
            member_id="10324",
            user_id="local",
        )

    assert exc_info.value.code == "glossary_alias_exists"


class FailingCatalogClient:
    async def fetch_members(self):
        raise AppError(
            "member_catalog_transport_error",
            "官方目录暂时不可用",
            True,
        )


@pytest.mark.asyncio
async def test_sync_failure_keeps_last_successful_catalog(settings, repository):
    repository.replace_member_catalog(
        [
            MemberCatalogEntry(
                member_id="10337",
                canonical_name="曹可甜",
                status="99",
                active=True,
            )
        ],
        source_url=settings.member_catalog_url,
        source_hash="d" * 64,
    )
    service = MemberCatalogService(
        settings,
        repository,
        FailingCatalogClient(),
    )

    with pytest.raises(AppError):
        await service.sync_if_due(force=True)

    state = repository.get_glossary_sync_state()
    member = repository.get_member_catalog("10337")
    assert state.sync_status == "failed"
    assert state.last_success_at is not None
    assert state.last_error == "官方目录暂时不可用"
    assert member and member.active is True
