"""SQL schema for per-user app tables.

Tables we own that are scoped to a user via FK to ``"user"."id"``:
``user_settings``, ``user_lrc_override``, ``lyric_mark``, ``songset_share``.

``songset_share`` is schema-only for now: the webapp will mint/revoke tokens
and serve ``/share/[token]``. ``render_job_id`` is plain TEXT because the
``render_jobs`` table does not exist yet; a FK will be added when it lands.
"""

CREATE_USER_SETTINGS_TABLE = """
CREATE TABLE IF NOT EXISTS user_settings (
    user_id            BIGINT PRIMARY KEY REFERENCES "user"("id") ON DELETE CASCADE,
    offline_auto_cache BOOLEAN NOT NULL DEFAULT TRUE,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

CREATE_USER_LRC_OVERRIDE_TABLE = """
CREATE TABLE IF NOT EXISTS user_lrc_override (
    id                     TEXT PRIMARY KEY,
    user_id                BIGINT NOT NULL REFERENCES "user"("id") ON DELETE CASCADE,
    recording_content_hash TEXT NOT NULL REFERENCES recordings(content_hash) ON DELETE CASCADE,
    lrc_content            TEXT NOT NULL,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, recording_content_hash)
);
"""

CREATE_LYRIC_MARK_TABLE = """
CREATE TABLE IF NOT EXISTS lyric_mark (
    id                     TEXT PRIMARY KEY,
    user_id                BIGINT NOT NULL REFERENCES "user"("id") ON DELETE CASCADE,
    recording_content_hash TEXT NOT NULL REFERENCES recordings(content_hash) ON DELETE CASCADE,
    timestamp_seconds      DOUBLE PRECISION NOT NULL,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, recording_content_hash, timestamp_seconds)
);
"""

# render_job_id is intentionally TEXT (not a FK) — the render_jobs table
# does not exist yet. Add the FK in a follow-up when render_jobs lands.
CREATE_SONGSET_SHARE_TABLE = """
CREATE TABLE IF NOT EXISTS songset_share (
    token              TEXT PRIMARY KEY,
    songset_id         TEXT NOT NULL REFERENCES songsets(id) ON DELETE CASCADE,
    render_job_id      TEXT NOT NULL,
    created_by_user_id BIGINT NOT NULL REFERENCES "user"("id") ON DELETE CASCADE,
    allow_download     BOOLEAN NOT NULL DEFAULT FALSE,
    expires_at         TIMESTAMPTZ,
    revoked_at         TIMESTAMPTZ,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

CREATE_USER_DATA_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_user_lrc_override_user ON user_lrc_override(user_id);",
    "CREATE INDEX IF NOT EXISTS idx_user_lrc_override_recording "
    "ON user_lrc_override(recording_content_hash);",
    "CREATE INDEX IF NOT EXISTS idx_lyric_mark_user ON lyric_mark(user_id);",
    "CREATE INDEX IF NOT EXISTS idx_lyric_mark_recording "
    "ON lyric_mark(recording_content_hash);",
    "CREATE INDEX IF NOT EXISTS idx_songset_share_songset ON songset_share(songset_id);",
    "CREATE INDEX IF NOT EXISTS idx_songset_share_creator "
    "ON songset_share(created_by_user_id);",
]

CREATE_USER_SETTINGS_UPDATE_TRIGGER = """
DROP TRIGGER IF EXISTS trg_user_settings_updated_at ON user_settings;
CREATE TRIGGER trg_user_settings_updated_at
    BEFORE UPDATE ON user_settings
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
"""

CREATE_USER_LRC_OVERRIDE_UPDATE_TRIGGER = """
DROP TRIGGER IF EXISTS trg_user_lrc_override_updated_at ON user_lrc_override;
CREATE TRIGGER trg_user_lrc_override_updated_at
    BEFORE UPDATE ON user_lrc_override
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
"""

ALL_USER_DATA_SCHEMA_STATEMENTS = [
    CREATE_USER_SETTINGS_TABLE,
    CREATE_USER_LRC_OVERRIDE_TABLE,
    CREATE_LYRIC_MARK_TABLE,
    CREATE_SONGSET_SHARE_TABLE,
    *CREATE_USER_DATA_INDEXES,
    CREATE_USER_SETTINGS_UPDATE_TRIGGER,
    CREATE_USER_LRC_OVERRIDE_UPDATE_TRIGGER,
]
