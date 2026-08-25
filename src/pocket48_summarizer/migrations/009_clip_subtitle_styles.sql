ALTER TABLE video_clip_exports
ADD COLUMN subtitle_font_scale INTEGER NOT NULL DEFAULT 100
CHECK (subtitle_font_scale BETWEEN 70 AND 160);

ALTER TABLE video_clip_exports
ADD COLUMN subtitle_text_color TEXT NOT NULL DEFAULT '#FFFFFF'
CHECK (
    length(subtitle_text_color) = 7
    AND subtitle_text_color GLOB '#[0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f]'
);

ALTER TABLE video_clip_exports
ADD COLUMN subtitle_background_color TEXT NOT NULL DEFAULT '#000000'
CHECK (
    length(subtitle_background_color) = 7
    AND subtitle_background_color GLOB '#[0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f]'
);
