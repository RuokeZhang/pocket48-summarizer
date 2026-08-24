import pytest

from pocket48_summarizer.errors import AppError
from pocket48_summarizer.models import TranscriptSegment
from pocket48_summarizer.translation import SubtitleTranslationService


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    async def chat_json(
        self, *, system_prompt, user_prompt, response_model=None
    ):
        self.prompts.append((system_prompt, user_prompt, response_model))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def completed_job_with_transcript(repository, live_id="900001"):
    job, _ = repository.create_or_get_job(
        (
            "https://h5.48.cn/2019appshare/memberLiveShare/"
            f"index.html?id={live_id}"
        ),
        live_id,
    )
    claimed = repository.claim_next_job("main-worker", 120)
    assert claimed and claimed.id == job.id
    repository.replace_transcript(
        job.id,
        [
            TranscriptSegment(
                sequence=1,
                start_ms=0,
                end_ms=1000,
                text="第一句",
            ),
            TranscriptSegment(
                sequence=2,
                start_ms=1000,
                end_ms=2000,
                text="第二句",
            ),
            TranscriptSegment(
                sequence=3,
                start_ms=2000,
                end_ms=3000,
                text="第三句",
            ),
        ],
    )
    repository.mark_completed(job.id)
    return job


def test_translation_queue_is_idempotent_and_recovers_expired_lease(
    repository
):
    job = completed_job_with_transcript(repository)

    first = repository.request_subtitle_translation(job.id)
    second = repository.request_subtitle_translation(job.id)
    assert first.status == "queued"
    assert second.status == "queued"

    claimed = repository.claim_next_subtitle_translation("worker-1", 120)
    assert claimed and claimed.job_id == job.id
    assert claimed.retry_count == 1
    assert (
        repository.claim_next_subtitle_translation("worker-2", 120)
        is None
    )

    with repository.database.connect() as connection:
        connection.execute(
            """
            UPDATE subtitle_translation_requests
            SET lease_expires_at = '2000-01-01T00:00:00+00:00'
            WHERE job_id = ? AND language = 'en'
            """,
            (job.id,),
        )

    assert repository.recover_expired_subtitle_translations() == 1
    reclaimed = repository.claim_next_subtitle_translation("worker-2", 120)
    assert reclaimed and reclaimed.job_id == job.id
    assert reclaimed.retry_count == 2


@pytest.mark.asyncio
async def test_translation_resumes_from_persisted_segments(repository):
    job = completed_job_with_transcript(repository, "900002")
    repository.save_transcript_translations(
        job.id,
        "en",
        {1: "First sentence."},
    )
    llm = FakeLLM(
        [
            {
                "translations": [
                    {"sequence": 2, "text": "Second sentence."},
                    {"sequence": 3, "text": "Third sentence."},
                ]
            }
        ]
    )
    service = SubtitleTranslationService(
        repository,
        llm,
        max_input_chars=1000,
    )

    await service.translate_job(job.id)

    assert repository.get_transcript_translations(job.id) == {
        1: "First sentence.",
        2: "Second sentence.",
        3: "Third sentence.",
    }
    assert '"sequence": 1' not in llm.prompts[0][1]
    assert llm.prompts[0][2].__name__ == "TranslationBatch"


@pytest.mark.asyncio
async def test_translation_persists_completed_batches_before_failure(
    repository
):
    job = completed_job_with_transcript(repository, "900003")
    llm = FakeLLM(
        [
            {
                "translations": [
                    {"sequence": 1, "text": "First sentence."}
                ]
            },
            AppError("llm_request_failed", "temporary failure", True),
        ]
    )
    service = SubtitleTranslationService(
        repository,
        llm,
        max_input_chars=4,
    )

    with pytest.raises(AppError, match="temporary failure"):
        await service.translate_job(job.id)

    assert repository.get_transcript_translations(job.id) == {
        1: "First sentence."
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {
            "translations": [
                {"sequence": 1, "text": "First."},
                {"sequence": 1, "text": "Duplicate."},
                {"sequence": 3, "text": "Third."},
            ]
        },
        {
            "translations": [
                {"sequence": 1, "text": "First."},
                {"sequence": 2, "text": "Second."},
            ]
        },
        {"translations": "not-a-list"},
    ],
)
async def test_translation_rejects_incomplete_or_invalid_responses(
    repository, payload
):
    job = completed_job_with_transcript(repository)
    service = SubtitleTranslationService(
        repository,
        FakeLLM([payload]),
        max_input_chars=1000,
    )

    with pytest.raises(AppError) as error:
        await service.translate_job(job.id)

    assert error.value.code == "translation_invalid_response"
