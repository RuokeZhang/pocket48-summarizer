ALTER TABLE video_clip_exports
ADD COLUMN output_layout TEXT NOT NULL DEFAULT 'portrait'
CHECK (output_layout IN ('portrait', 'landscape'));
