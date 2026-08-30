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


def _idft_catalog():
    return [
        MemberCatalogEntry(
            member_id="10337",
            canonical_name="曹可甜",
            group_id="10",
            group_name="SNH",
            status="99",
            active=True,
        ),
        MemberCatalogEntry(
            member_id="70001",
            canonical_name="工厂甲",
            group_id="70",
            group_name="IDFT",
            status="99",
            active=True,
        ),
        MemberCatalogEntry(
            member_id="70002",
            canonical_name="工厂乙",
            group_id="70",
            group_name="IDFT",
            status="99",
            active=True,
        ),
    ]


def test_disabling_a_group_removes_only_that_group_from_the_glossary(
    repository,
):
    repository.replace_member_catalog(
        _idft_catalog(),
        source_url="https://h5.48.cn/catalog",
        source_hash="d" * 64,
    )
    before = repository.get_glossary_sync_state().glossary_fingerprint

    changed = repository.set_group_admin_disabled("70", disabled=True)

    vocabulary = repository.list_active_vocabulary_texts()
    assert changed == 2
    assert "曹可甜" in vocabulary
    assert "工厂甲" not in vocabulary
    assert "工厂乙" not in vocabulary
    assert "IDFT" not in vocabulary
    assert repository.get_glossary_sync_state().glossary_fingerprint != before


def test_a_catalog_sync_does_not_revive_administratively_disabled_members(
    repository,
):
    """The feed owns `active` and rewrites it on every sync.

    Without a column of its own the administrator's decision would silently
    come undone the next time the catalog refreshed.
    """

    catalog = _idft_catalog()
    repository.replace_member_catalog(
        catalog,
        source_url="https://h5.48.cn/catalog",
        source_hash="e" * 64,
    )
    repository.set_group_admin_disabled("70", disabled=True)

    state = repository.replace_member_catalog(
        catalog,
        source_url="https://h5.48.cn/catalog",
        source_hash="f" * 64,
    )

    disabled = repository.get_member_catalog("70001")
    assert disabled is not None
    assert disabled.admin_disabled is True
    assert disabled.source_active is True
    assert disabled.active is False
    assert "工厂甲" not in repository.list_active_vocabulary_texts()
    assert state.member_count == 3
    assert state.active_member_count == 1


def test_re_enabling_does_not_activate_a_member_the_feed_dropped(repository):
    """Restoring an override must hand ownership back to the feed."""

    catalog = _idft_catalog()
    repository.replace_member_catalog(
        catalog,
        source_url="https://h5.48.cn/catalog",
        source_hash="0" * 64,
    )
    repository.set_group_admin_disabled("70", disabled=True)
    repository.replace_member_catalog(
        [
            catalog[0],
            catalog[1].model_copy(update={"active": False}),
            catalog[2],
        ],
        source_url="https://h5.48.cn/catalog",
        source_hash="1" * 64,
    )

    repository.set_group_admin_disabled("70", disabled=False)

    graduated = repository.get_member_catalog("70001")
    restored = repository.get_member_catalog("70002")
    assert graduated is not None and graduated.active is False
    assert restored is not None and restored.active is True


def test_a_single_member_can_be_disabled_and_restored(repository):
    repository.replace_member_catalog(
        _idft_catalog(),
        source_url="https://h5.48.cn/catalog",
        source_hash="2" * 64,
    )

    repository.set_member_admin_disabled("10337", disabled=True)
    assert "曹可甜" not in repository.list_active_vocabulary_texts()

    repository.set_member_admin_disabled("10337", disabled=False)
    assert "曹可甜" in repository.list_active_vocabulary_texts()

    with pytest.raises(AppError) as exc_info:
        repository.set_member_admin_disabled("missing", disabled=True)
    assert exc_info.value.code == "member_catalog_member_not_found"


def test_group_summary_reports_catalog_and_glossary_counts(repository):
    repository.replace_member_catalog(
        _idft_catalog(),
        source_url="https://h5.48.cn/catalog",
        source_hash="3" * 64,
    )
    repository.set_group_admin_disabled("70", disabled=True)

    groups = {
        group.group_id: group
        for group in repository.list_member_catalog_groups()
    }
    assert groups["70"].group_name == "IDFT"
    assert groups["70"].member_count == 2
    assert groups["70"].disabled_count == 2
    assert groups["70"].active_count == 0
    assert groups["10"].active_count == 1


def test_the_reported_member_count_follows_an_override_immediately(
    repository,
):
    """The headline count must not wait for the next catalog sync.

    A number that stays put after disabling a group reads as the override
    having silently failed.
    """

    repository.replace_member_catalog(
        _idft_catalog(),
        source_url="https://h5.48.cn/catalog",
        source_hash="4" * 64,
    )
    assert repository.get_glossary_sync_state().active_member_count == 3

    repository.set_group_admin_disabled("70", disabled=True)
    assert repository.get_glossary_sync_state().active_member_count == 1

    repository.set_member_admin_disabled("10337", disabled=True)
    assert repository.get_glossary_sync_state().active_member_count == 0

    repository.set_group_admin_disabled("70", disabled=False)
    assert repository.get_glossary_sync_state().active_member_count == 2


def _trainee_catalog():
    return [
        MemberCatalogEntry(
            member_id="10001",
            canonical_name="上海预备甲",
            group_id="10",
            group_name="SNH",
            team_name="S预备生",
            status="99",
            active=True,
        ),
        MemberCatalogEntry(
            member_id="30001",
            canonical_name="广州预备甲",
            group_id="30",
            group_name="GNZ",
            team_name="G预备生",
            status="99",
            active=True,
        ),
        MemberCatalogEntry(
            member_id="30002",
            canonical_name="广州预备乙",
            group_id="30",
            group_name="GNZ",
            team_name="G预备生",
            status="44",
            active=False,
        ),
        MemberCatalogEntry(
            member_id="30003",
            canonical_name="广州正选",
            group_id="30",
            group_name="GNZ",
            team_name="NIII",
            status="99",
            active=True,
        ),
    ]


def _synced_trainees(repository):
    repository.replace_member_catalog(
        _trainee_catalog(),
        source_url="https://h5.48.cn/catalog",
        source_hash="e" * 64,
    )


def test_disabling_a_team_leaves_the_rest_of_its_group_alone(repository):
    _synced_trainees(repository)

    changed = repository.set_team_admin_disabled(
        "30", "G预备生", disabled=True
    )

    assert changed == 2
    states = {
        member.member_id: member
        for member in repository.list_member_catalog()
    }
    assert states["30001"].admin_disabled is True
    assert states["30001"].active is False
    assert states["30003"].admin_disabled is False
    assert states["30003"].active is True
    # The same team name exists under other groups, so a team switch that
    # ignored the group would quietly disable the wrong people.
    assert states["10001"].active is True


def test_team_counts_report_what_the_switch_will_actually_change(repository):
    _synced_trainees(repository)

    teams = {
        (team.group_id, team.team_name): team
        for team in repository.list_member_catalog_teams()
    }

    trainees = teams[("30", "G预备生")]
    assert trainees.group_name == "GNZ"
    assert trainees.member_count == 2
    # One of the two is already retired upstream, so only one name actually
    # leaves the glossary.
    assert trainees.active_count == 1
    assert trainees.disabled_count == 0


def test_re_enabling_a_team_does_not_revive_retired_members(repository):
    _synced_trainees(repository)
    repository.set_team_admin_disabled("30", "G预备生", disabled=True)

    repository.set_team_admin_disabled("30", "G预备生", disabled=False)

    states = {
        member.member_id: member
        for member in repository.list_member_catalog()
    }
    assert states["30001"].active is True
    assert states["30002"].active is False


def test_a_team_switch_needs_both_a_group_and_a_team(repository):
    _synced_trainees(repository)

    with pytest.raises(AppError):
        repository.set_team_admin_disabled("30", "  ", disabled=True)
