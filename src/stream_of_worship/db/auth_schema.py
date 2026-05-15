"""SQL schema for Better Auth core tables.

Defines the canonical Better Auth schema shape (user, account, session,
verification) so the future Next.js webapp can plug in Better Auth without
schema migrations. Identifiers are camelCase quoted to match Better Auth's
default schema; columns use `BIGINT GENERATED ALWAYS AS IDENTITY` so user
IDs are short, sequential integers (e.g. "User 12"). The webapp must set
``advanced.database.useNumberId: true`` in its Better Auth config to match.
"""

CREATE_USER_TABLE = """
CREATE TABLE IF NOT EXISTS "user" (
    "id"            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    "name"          TEXT NOT NULL,
    "email"         TEXT NOT NULL UNIQUE,
    "emailVerified" BOOLEAN NOT NULL DEFAULT FALSE,
    "image"         TEXT,
    "createdAt"     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    "updatedAt"     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

CREATE_ACCOUNT_TABLE = """
CREATE TABLE IF NOT EXISTS "account" (
    "id"                    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    "userId"                BIGINT NOT NULL REFERENCES "user"("id") ON DELETE CASCADE,
    "accountId"             TEXT NOT NULL,
    "providerId"            TEXT NOT NULL,
    "accessToken"           TEXT,
    "refreshToken"          TEXT,
    "idToken"               TEXT,
    "accessTokenExpiresAt"  TIMESTAMPTZ,
    "refreshTokenExpiresAt" TIMESTAMPTZ,
    "scope"                 TEXT,
    "password"              TEXT,
    "createdAt"             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    "updatedAt"             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE ("providerId", "accountId")
);
"""

CREATE_SESSION_TABLE = """
CREATE TABLE IF NOT EXISTS "session" (
    "id"        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    "userId"    BIGINT NOT NULL REFERENCES "user"("id") ON DELETE CASCADE,
    "token"     TEXT NOT NULL UNIQUE,
    "expiresAt" TIMESTAMPTZ NOT NULL,
    "ipAddress" TEXT,
    "userAgent" TEXT,
    "createdAt" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    "updatedAt" TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

CREATE_VERIFICATION_TABLE = """
CREATE TABLE IF NOT EXISTS "verification" (
    "id"         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    "identifier" TEXT NOT NULL,
    "value"      TEXT NOT NULL,
    "expiresAt"  TIMESTAMPTZ NOT NULL,
    "createdAt"  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    "updatedAt"  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

CREATE_AUTH_INDEXES = [
    'CREATE INDEX IF NOT EXISTS idx_account_user_id ON "account"("userId");',
    'CREATE INDEX IF NOT EXISTS idx_session_user_id ON "session"("userId");',
    'CREATE INDEX IF NOT EXISTS idx_session_token   ON "session"("token");',
    'CREATE INDEX IF NOT EXISTS idx_verification_identifier ON "verification"("identifier");',
]

# Trigger function for camelCase "updatedAt" columns.
CREATE_UPDATEDAT_TIMESTAMP_FUNCTION = """
CREATE OR REPLACE FUNCTION update_updatedat_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW."updatedAt" = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';
"""

CREATE_USER_UPDATEDAT_TRIGGER = """
DROP TRIGGER IF EXISTS trg_user_updatedat ON "user";
CREATE TRIGGER trg_user_updatedat
    BEFORE UPDATE ON "user"
    FOR EACH ROW
    EXECUTE FUNCTION update_updatedat_column();
"""

CREATE_ACCOUNT_UPDATEDAT_TRIGGER = """
DROP TRIGGER IF EXISTS trg_account_updatedat ON "account";
CREATE TRIGGER trg_account_updatedat
    BEFORE UPDATE ON "account"
    FOR EACH ROW
    EXECUTE FUNCTION update_updatedat_column();
"""

CREATE_SESSION_UPDATEDAT_TRIGGER = """
DROP TRIGGER IF EXISTS trg_session_updatedat ON "session";
CREATE TRIGGER trg_session_updatedat
    BEFORE UPDATE ON "session"
    FOR EACH ROW
    EXECUTE FUNCTION update_updatedat_column();
"""

CREATE_VERIFICATION_UPDATEDAT_TRIGGER = """
DROP TRIGGER IF EXISTS trg_verification_updatedat ON "verification";
CREATE TRIGGER trg_verification_updatedat
    BEFORE UPDATE ON "verification"
    FOR EACH ROW
    EXECUTE FUNCTION update_updatedat_column();
"""

ALL_AUTH_SCHEMA_STATEMENTS = [
    CREATE_USER_TABLE,
    CREATE_ACCOUNT_TABLE,
    CREATE_SESSION_TABLE,
    CREATE_VERIFICATION_TABLE,
    *CREATE_AUTH_INDEXES,
    CREATE_UPDATEDAT_TIMESTAMP_FUNCTION,
    CREATE_USER_UPDATEDAT_TRIGGER,
    CREATE_ACCOUNT_UPDATEDAT_TRIGGER,
    CREATE_SESSION_UPDATEDAT_TRIGGER,
    CREATE_VERIFICATION_UPDATEDAT_TRIGGER,
]
