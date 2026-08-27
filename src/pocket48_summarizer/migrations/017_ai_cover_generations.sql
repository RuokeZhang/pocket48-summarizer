CREATE TABLE IF NOT EXISTS ai_cover_generations (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    timeline_index INTEGER NOT NULL,
    requested_by_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    request_id TEXT NOT NULL,
    source_timestamp_ms INTEGER NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    shared_seed INTEGER,
    layout_style TEXT NOT NULL DEFAULT 'sticker_pop',
    title_text TEXT NOT NULL,
    highlight_text TEXT NOT NULL DEFAULT '',
    extra_text_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE (job_id, request_id),
    CHECK (timeline_index >= 0),
    CHECK (source_timestamp_ms >= 0),
    CHECK (
        layout_style IN (
            'sticker_pop',
            'editorial_arc',
            'banner_energy'
        )
    ),
    CHECK (length(title_text) BETWEEN 1 AND 80),
    CHECK (length(highlight_text) <= 60),
    CHECK (json_valid(extra_text_json)),
    CHECK (status IN ('queued', 'running', 'completed', 'failed'))
);

CREATE INDEX IF NOT EXISTS ai_cover_generations_job_timeline_created_idx
    ON ai_cover_generations (job_id, timeline_index, created_at DESC);

CREATE INDEX IF NOT EXISTS ai_cover_generations_status_updated_idx
    ON ai_cover_generations (status, updated_at);

CREATE TABLE IF NOT EXISTS ai_cover_assets (
    id TEXT PRIMARY KEY,
    generation_id TEXT NOT NULL
        REFERENCES ai_cover_generations(id) ON DELETE CASCADE,
    orientation TEXT NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    status TEXT NOT NULL,
    provider_task_id TEXT,
    provider_request_id TEXT,
    background_oss_object_key TEXT,
    final_oss_object_key TEXT,
    background_sha256 TEXT,
    final_sha256 TEXT,
    text_revision INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE (generation_id, orientation),
    CHECK (orientation IN ('landscape', 'four_three')),
    CHECK (width > 0 AND width <= 8192),
    CHECK (height > 0 AND height <= 8192),
    CHECK (status IN ('queued', 'running', 'completed', 'failed')),
    CHECK (text_revision >= 0)
);

CREATE INDEX IF NOT EXISTS ai_cover_assets_generation_orientation_idx
    ON ai_cover_assets (generation_id, orientation);

ALTER TABLE video_clip_exports
ADD COLUMN ai_cover_generation_id TEXT
REFERENCES ai_cover_generations(id) ON DELETE SET NULL;

ALTER TABLE video_clip_exports
ADD COLUMN ai_cover_asset_id TEXT
REFERENCES ai_cover_assets(id) ON DELETE SET NULL;

ALTER TABLE video_clip_exports
ADD COLUMN ai_cover_final_oss_object_key TEXT;

ALTER TABLE video_clip_exports
ADD COLUMN ai_cover_final_sha256 TEXT;

ALTER TABLE video_clip_exports
ADD COLUMN ai_cover_text_revision INTEGER
CHECK (
    ai_cover_text_revision IS NULL
    OR ai_cover_text_revision >= 0
);

CREATE INDEX IF NOT EXISTS video_clip_exports_ai_cover_generation_idx
    ON video_clip_exports (ai_cover_generation_id);

CREATE INDEX IF NOT EXISTS video_clip_exports_ai_cover_asset_idx
    ON video_clip_exports (ai_cover_asset_id);
