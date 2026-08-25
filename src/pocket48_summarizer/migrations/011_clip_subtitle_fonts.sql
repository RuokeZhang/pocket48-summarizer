ALTER TABLE video_clip_exports
ADD COLUMN subtitle_font_family TEXT NOT NULL DEFAULT 'sans'
CHECK (subtitle_font_family IN ('wenkai', 'serif', 'sans'));
