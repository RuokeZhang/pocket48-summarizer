UPDATE jobs
SET
    status = 'queued',
    stage = 'queued',
    progress_percent = 0,
    progress_message = '等待细化长直播时间线',
    summary_json = NULL,
    summary_markdown = NULL,
    error_code = NULL,
    error_message = NULL,
    error_retryable = 0,
    worker_id = NULL,
    lease_expires_at = NULL,
    completed_at = NULL,
    updated_at = datetime('now')
WHERE status = 'completed'
AND summary_json IS NOT NULL
AND (
    SELECT COALESCE(MAX(end_ms), 0)
    FROM transcript_segments
    WHERE transcript_segments.job_id = jobs.id
) >= 7200000
AND EXISTS (
    SELECT 1
    FROM json_each(jobs.summary_json, '$.timeline')
    WHERE (
        CAST(json_extract(value, '$.end_ms') AS INTEGER)
        - CAST(json_extract(value, '$.start_ms') AS INTEGER)
    ) > 600000
);
