ALTER TABLE video_clip_exports
ADD COLUMN kept_ranges_json TEXT NOT NULL DEFAULT '[]';

UPDATE video_clip_exports
SET kept_ranges_json = json_array(
    json_object(
        'start_ms', start_ms,
        'end_ms', end_ms
    )
)
WHERE kept_ranges_json = '[]';
