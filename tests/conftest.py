from __future__ import annotations

import sys

import pytest

from pocket48_summarizer.config import Settings
from pocket48_summarizer.db import Database
from pocket48_summarizer.repository import JobRepository


@pytest.fixture
def settings(tmp_path):
    return Settings(
        data_dir=tmp_path,
        ffmpeg_path=sys.executable,
        enable_worker=False,
        aliyun_access_key_id="test-id",
        aliyun_access_key_secret="test-secret",
        aliyun_oss_endpoint="https://oss-cn-beijing.aliyuncs.com",
        aliyun_oss_bucket="test-bucket",
        dashscope_api_key="test-dashscope",
        llm_base_url="https://llm.example/v1",
        llm_api_key="test-llm",
        llm_model="test-model",
        external_retry_attempts=1,
    )


@pytest.fixture
def repository(settings):
    settings.prepare_directories()
    database = Database(settings.database_path)
    database.initialize()
    return JobRepository(database)
