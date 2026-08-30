ALTER TABLE video_clip_exports
ADD COLUMN landscape_theme TEXT NOT NULL DEFAULT 'cream'
CHECK (landscape_theme IN
    ('cream', 'denim', 'mint', 'sakura', 'matcha', 'ink'));
