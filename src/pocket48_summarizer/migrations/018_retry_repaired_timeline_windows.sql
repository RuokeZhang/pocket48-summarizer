UPDATE jobs
SET
    status = 'queued',
    stage = 'queued',
    progress_percent = 0,
    progress_message = '等待重新校准时间线时间戳',
    error_code = NULL,
    error_message = NULL,
    error_retryable = 0,
    worker_id = NULL,
    lease_expires_at = NULL,
    started_at = NULL,
    completed_at = NULL,
    updated_at = datetime('now')
WHERE status = 'failed'
AND error_code IN (
    'llm_timeline_evidence_mismatch',
    'llm_invalid_chunk_window',
    'llm_timeline_too_coarse'
);
