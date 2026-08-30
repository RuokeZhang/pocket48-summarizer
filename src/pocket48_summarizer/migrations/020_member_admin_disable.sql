-- The official feed owns whether a member is active, and every catalog sync
-- overwrites `active` from it, so an administrator's decision to drop a member
-- from the glossary needs a column the sync will not clobber.
--
-- `active` deliberately stays the single effective flag that every existing
-- glossary, vocabulary and fingerprint query already reads. It is recomputed
-- as `source_active AND NOT admin_disabled` wherever either input changes, so
-- no read site has to learn about the override and none can forget it.
ALTER TABLE member_catalog
    ADD COLUMN source_active INTEGER NOT NULL DEFAULT 0;

ALTER TABLE member_catalog
    ADD COLUMN admin_disabled INTEGER NOT NULL DEFAULT 0;

-- Before this migration nothing could disable a member, so whatever `active`
-- holds today is exactly what the feed last said.
UPDATE member_catalog SET source_active = active;

CREATE INDEX member_catalog_group_disabled_idx
    ON member_catalog (group_id, admin_disabled);
