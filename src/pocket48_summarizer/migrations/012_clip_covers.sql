ALTER TABLE video_clip_exports
ADD COLUMN subtitle_font_percent INTEGER NOT NULL DEFAULT 100
CHECK (subtitle_font_percent BETWEEN 50 AND 150);

UPDATE video_clip_exports
SET subtitle_font_percent = MAX(
    50,
    MIN(
        150,
        CAST(ROUND(subtitle_font_scale * 100.0 / 160.0) AS INTEGER)
    )
);

CREATE TRIGGER IF NOT EXISTS video_clip_exports_legacy_font_insert
AFTER INSERT ON video_clip_exports
WHEN NEW.subtitle_font_percent = 100
BEGIN
    UPDATE video_clip_exports
    SET subtitle_font_percent = MAX(
        50,
        MIN(
            150,
            CAST(
                ROUND(NEW.subtitle_font_scale * 100.0 / 160.0)
                AS INTEGER
            )
        )
    )
    WHERE id = NEW.id;
END;

ALTER TABLE video_clip_exports
ADD COLUMN cover_enabled INTEGER NOT NULL DEFAULT 0
CHECK (cover_enabled IN (0, 1));

ALTER TABLE video_clip_exports
ADD COLUMN cover_timestamp_ms INTEGER
CHECK (cover_timestamp_ms IS NULL OR cover_timestamp_ms >= 0);

ALTER TABLE video_clip_exports
ADD COLUMN cover_title TEXT NOT NULL DEFAULT ''
CHECK (length(cover_title) <= 40);

ALTER TABLE video_clip_exports
ADD COLUMN cover_style TEXT NOT NULL DEFAULT 'scrim'
CHECK (cover_style IN ('scrim', 'display', 'badge'));
