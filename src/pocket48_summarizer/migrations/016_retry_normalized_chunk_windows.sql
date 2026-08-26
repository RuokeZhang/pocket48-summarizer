UPDATE jobs
SET
    status = 'queued',
    stage = 'queued',
    progress_percent = 0,
    progress_message = '等待继续细化长直播时间线',
    error_code = NULL,
    error_message = NULL,
    error_retryable = 0,
    worker_id = NULL,
    lease_expires_at = NULL,
    started_at = NULL,
    completed_at = NULL,
    updated_at = datetime('now')
WHERE status = 'failed'
AND error_code = 'llm_invalid_chunk_window'
AND (
    SELECT COALESCE(MAX(end_ms), 0)
    FROM transcript_segments
    WHERE transcript_segments.job_id = jobs.id
) >= 7200000;
