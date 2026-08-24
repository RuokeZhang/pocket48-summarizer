CREATE TABLE member_catalog (
    member_id TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    pinyin TEXT NOT NULL DEFAULT '',
    group_id TEXT NOT NULL DEFAULT '',
    group_name TEXT NOT NULL DEFAULT '',
    team_id TEXT NOT NULL DEFAULT '',
    team_name TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    ranking INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 0,
    source_present INTEGER NOT NULL DEFAULT 1,
    source TEXT NOT NULL DEFAULT 'snh48_official',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    CHECK (active IN (0, 1)),
    CHECK (source_present IN (0, 1))
);

CREATE INDEX member_catalog_name_idx
    ON member_catalog (canonical_name);

CREATE INDEX member_catalog_team_idx
    ON member_catalog (group_id, team_id, active);

CREATE INDEX member_catalog_active_idx
    ON member_catalog (active, canonical_name);

CREATE TABLE glossary_terms (
    id TEXT PRIMARY KEY,
    canonical_text TEXT NOT NULL,
    canonical_normalized TEXT NOT NULL,
    term_type TEXT NOT NULL,
    description_zh TEXT NOT NULL DEFAULT '',
    description_en TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'admin',
    active INTEGER NOT NULL DEFAULT 1,
    created_by_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (active IN (0, 1)),
    UNIQUE (term_type, canonical_normalized)
);

CREATE INDEX glossary_terms_active_type_idx
    ON glossary_terms (active, term_type, canonical_text);

CREATE TABLE glossary_aliases (
    id TEXT PRIMARY KEY,
    member_id TEXT REFERENCES member_catalog(member_id) ON DELETE CASCADE,
    term_id TEXT REFERENCES glossary_terms(id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    alias_normalized TEXT NOT NULL UNIQUE,
    active INTEGER NOT NULL DEFAULT 1,
    created_by_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (active IN (0, 1)),
    CHECK (
        (member_id IS NOT NULL AND term_id IS NULL)
        OR (member_id IS NULL AND term_id IS NOT NULL)
    )
);

CREATE INDEX glossary_aliases_member_idx
    ON glossary_aliases (member_id, active);

CREATE INDEX glossary_aliases_term_idx
    ON glossary_aliases (term_id, active);

CREATE TABLE glossary_sync_state (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    source_url TEXT NOT NULL,
    sync_status TEXT NOT NULL DEFAULT 'never',
    source_hash TEXT,
    catalog_version TEXT,
    glossary_fingerprint TEXT,
    member_count INTEGER NOT NULL DEFAULT 0,
    active_member_count INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TEXT,
    last_success_at TEXT,
    last_error TEXT,
    active_vocabulary_id TEXT,
    vocabulary_fingerprint TEXT,
    vocabulary_updated_at TEXT,
    vocabulary_error TEXT
);

INSERT INTO glossary_sync_state (
    singleton,
    source_url,
    sync_status
) VALUES (
    1,
    'https://h5.48.cn/resource/jsonp/allmembers_simple.php?gid=00',
    'never'
);
