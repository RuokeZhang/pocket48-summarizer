INSERT INTO subtitle_translation_requests (
    job_id,
    language,
    status,
    retry_count,
    requested_at,
    updated_at
)
SELECT
    jobs.id,
    'en',
    'queued',
    0,
    COALESCE(jobs.completed_at, jobs.updated_at),
    strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')
FROM jobs
WHERE jobs.status = 'completed'
  AND EXISTS (
      SELECT 1
      FROM transcript_segments
      WHERE transcript_segments.job_id = jobs.id
  )
ON CONFLICT(job_id, language) DO NOTHING;

UPDATE subtitle_translation_requests
SET status = 'queued',
    retry_count = 0,
    error_message = NULL,
    worker_id = NULL,
    lease_expires_at = NULL,
    requested_at = strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'),
    updated_at = strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'),
    started_at = NULL,
    completed_at = NULL
WHERE status = 'failed'
  AND EXISTS (
      SELECT 1
      FROM jobs
      WHERE jobs.id = subtitle_translation_requests.job_id
        AND jobs.status = 'completed'
  );
