UPDATE jobs
SET
    status = 'queued',
    stage = 'queued',
    progress_percent = 0,
    progress_message = '等待重新生成完整时间线',
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
AND (
    SELECT COALESCE(
        MAX(CAST(json_extract(value, '$.end_ms') AS INTEGER)),
        0
    )
    FROM json_each(jobs.summary_json, '$.timeline')
) * 100
< (
    SELECT COALESCE(MAX(end_ms), 0)
    FROM transcript_segments
    WHERE transcript_segments.job_id = jobs.id
) * 65;
