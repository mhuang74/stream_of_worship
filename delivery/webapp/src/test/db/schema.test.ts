import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { getTableName } from "drizzle-orm";
import { getTableConfig } from "drizzle-orm/pg-core";
import {
  songs,
  recordings,
  users,
  accounts,
  sessions,
  verifications,
  songsets,
  songsetItems,
  renderJobs,
  songEmbeddings,
  songLineEmbeddings,
  userSettings,
  userLrcOverrides,
  lyricMarks,
  lyricsFeedback,
  songsetShares,
  glueTakes,
  glueJobs,
  gluePaidAttempts,
  glueExcerpts,
} from "@/db/schema";

// Helper to get column names from a drizzle table
function columnNames(table: Record<string, unknown>): string[] {
  return Object.entries(table)
    .filter(([, v]) => v !== null && typeof v === "object" && "name" in (v as object))
    .map(([, v]) => (v as { name: string }).name);
}

describe("schema: table names match SQL", () => {
  it("songs maps to 'songs'", () => expect(getTableName(songs)).toBe("songs"));
  it("recordings maps to 'recordings'", () => expect(getTableName(recordings)).toBe("recordings"));
  it("users (Better Auth) maps to 'user'", () => expect(getTableName(users)).toBe("user"));
  it("accounts (Better Auth) maps to 'account'", () =>
    expect(getTableName(accounts)).toBe("account"));
  it("sessions (Better Auth) maps to 'session'", () =>
    expect(getTableName(sessions)).toBe("session"));
  it("verifications (Better Auth) maps to 'verification'", () =>
    expect(getTableName(verifications)).toBe("verification"));
  it("songsets maps to 'songsets'", () => expect(getTableName(songsets)).toBe("songsets"));
  it("songsetItems maps to 'songset_items'", () =>
    expect(getTableName(songsetItems)).toBe("songset_items"));
  it("renderJobs maps to 'render_jobs'", () =>
    expect(getTableName(renderJobs)).toBe("render_jobs"));
  it("songEmbeddings maps to 'song_embedding'", () =>
    expect(getTableName(songEmbeddings)).toBe("song_embedding"));
  it("songLineEmbeddings maps to 'song_line_embedding'", () =>
    expect(getTableName(songLineEmbeddings)).toBe("song_line_embedding"));
  it("userSettings maps to 'user_settings'", () =>
    expect(getTableName(userSettings)).toBe("user_settings"));
  it("userLrcOverrides maps to 'user_lrc_override'", () =>
    expect(getTableName(userLrcOverrides)).toBe("user_lrc_override"));
  it("lyricMarks maps to 'lyric_mark'", () => expect(getTableName(lyricMarks)).toBe("lyric_mark"));
  it("songsetShares maps to 'songset_share'", () =>
    expect(getTableName(songsetShares)).toBe("songset_share"));
});

describe("schema: Better Auth camelCase columns", () => {
  it("users has camelCase columns for Better Auth", () => {
    const cols = columnNames(users);
    expect(cols).toContain("emailVerified");
    expect(cols).toContain("createdAt");
    expect(cols).toContain("updatedAt");
  });

  it("accounts has camelCase userId column", () => {
    const cols = columnNames(accounts);
    expect(cols).toContain("userId");
    expect(cols).toContain("accountId");
    expect(cols).toContain("providerId");
  });

  it("sessions has camelCase userId and expiresAt", () => {
    const cols = columnNames(sessions);
    expect(cols).toContain("userId");
    expect(cols).toContain("expiresAt");
  });
});

describe("schema: songsets has render tracking columns", () => {
  it("has latest_render_job_id column", () => {
    const cols = columnNames(songsets);
    expect(cols).toContain("latest_render_job_id");
  });

  it("has last_failed_render_job_id column", () => {
    const cols = columnNames(songsets);
    expect(cols).toContain("last_failed_render_job_id");
  });
});

describe("schema: render_jobs table structure", () => {
  it("has all plan-specified columns", () => {
    const cols = columnNames(renderJobs);
    expect(cols).toContain("font_size_preset");
    expect(cols).toContain("include_title_card");
    expect(cols).toContain("title_card_duration_seconds");
    expect(cols).toContain("chapters_r2_key");
  });

  it("has status tracking columns", () => {
    const cols = columnNames(renderJobs);
    expect(cols).toContain("status");
    expect(cols).toContain("phase");
    expect(cols).toContain("phase_index");
    expect(cols).toContain("total_phases");
    expect(cols).toContain("percent_complete");
    expect(cols).toContain("estimated_seconds_left");
    expect(cols).toContain("elapsed_seconds");
    expect(cols).toContain("estimated_total_seconds");
    expect(cols).toContain("total_duration_seconds");
    expect(cols).toContain("started_at");
  });

  it("has output R2 key columns", () => {
    const cols = columnNames(renderJobs);
    expect(cols).toContain("mp3_r2_key");
    expect(cols).toContain("mp4_r2_key");
    expect(cols).toContain("chapters_r2_key");
  });

  it("has render option columns", () => {
    const cols = columnNames(renderJobs);
    expect(cols).toContain("template");
    expect(cols).toContain("resolution");
    expect(cols).toContain("audio_enabled");
    expect(cols).toContain("video_enabled");
  });

  it("has snapshot columns for comparison dialog", () => {
    const cols = columnNames(renderJobs);
    expect(cols).toContain("song_count");
    expect(cols).toContain("songset_duration_seconds");
  });
});

describe("schema: song_embedding table structure", () => {
  it("has embedding vector column", () => {
    const cols = columnNames(songEmbeddings);
    expect(cols).toContain("embedding");
  });

  it("has song_id as primary key and model version", () => {
    const cols = columnNames(songEmbeddings);
    expect(cols).toContain("song_id");
    expect(cols).toContain("model_version");
  });

  it("has content_hash column", () => {
    const cols = columnNames(songEmbeddings);
    expect(cols).toContain("content_hash");
  });
});

describe("schema: song_line_embedding table structure", () => {
  it("maps to 'song_line_embedding'", () =>
    expect(getTableName(songLineEmbeddings)).toBe("song_line_embedding"));

  it("has embedding vector column", () => {
    const cols = columnNames(songLineEmbeddings);
    expect(cols).toContain("embedding");
  });

  it("has song_id, line_index, line_text, and model_version", () => {
    const cols = columnNames(songLineEmbeddings);
    expect(cols).toContain("song_id");
    expect(cols).toContain("line_index");
    expect(cols).toContain("line_text");
    expect(cols).toContain("model_version");
  });
});

describe("schema: per-user data tables", () => {
  it("userLrcOverrides has lrc_content", () => {
    const cols = columnNames(userLrcOverrides);
    expect(cols).toContain("lrc_content");
    expect(cols).toContain("recording_content_hash");
  });

  it("lyricMarks has timestamp_seconds", () => {
    const cols = columnNames(lyricMarks);
    expect(cols).toContain("timestamp_seconds");
    expect(cols).toContain("recording_content_hash");
  });

  it("lyricsFeedback maps to 'lyrics_feedback'", () =>
    expect(getTableName(lyricsFeedback)).toBe("lyrics_feedback"));

  it("lyricsFeedback has rating, reason, resolved_at, created_at, updated_at", () => {
    const cols = columnNames(lyricsFeedback);
    expect(cols).toEqual(
      expect.arrayContaining([
        "id",
        "user_id",
        "recording_content_hash",
        "rating",
        "reason",
        "note",
        "resolved_at",
        "created_at",
        "updated_at",
      ])
    );
  });

  it("lyricsFeedback has unique (user_id, recording_content_hash)", () => {
    const uniques = getTableConfig(lyricsFeedback).uniqueConstraints;
    const cols = uniques.flatMap((u) => u.columns.map((c) => c.name));
    expect(cols).toEqual(expect.arrayContaining(["user_id", "recording_content_hash"]));
  });

  it("lyricsFeedback userId references users.id and recording references recordings", () => {
    const userFk = findFkByColumnName(lyricsFeedback, "user_id");
    expect(userFk).toBeDefined();
    expect(getTableName(userFk!.reference().foreignTable)).toBe("user");
    const recordingFk = findFkByColumnName(lyricsFeedback, "recording_content_hash");
    expect(recordingFk).toBeDefined();
    expect(getTableName(recordingFk!.reference().foreignTable)).toBe("recordings");
  });

  it("songsetShares has token and allow_download", () => {
    const cols = columnNames(songsetShares);
    expect(cols).toContain("token");
    expect(cols).toContain("allow_download");
    expect(cols).toContain("render_job_id");
    expect(cols).toContain("revoked_at");
  });
});

function findFkByColumnName(table: Parameters<typeof getTableConfig>[0], colName: string) {
  const { foreignKeys } = getTableConfig(table);
  return foreignKeys.find((fk) => fk.reference().columns.some((c) => c.name === colName));
}

function indexNames(table: Parameters<typeof getTableConfig>[0]): string[] {
  return getTableConfig(table).indexes.map((idx) => idx.config.name);
}

describe("schema: foreign key references are defined", () => {
  it("accounts.userId references users.id", () => {
    const fk = findFkByColumnName(accounts, "userId");
    expect(fk).toBeDefined();
    expect(getTableName(fk!.reference().foreignTable)).toBe("user");
  });

  it("songsets.userId references users.id", () => {
    const fk = findFkByColumnName(songsets, "user_id");
    expect(fk).toBeDefined();
    expect(getTableName(fk!.reference().foreignTable)).toBe("user");
  });

  it("songsetItems.songsetId references songsets.id", () => {
    const fk = findFkByColumnName(songsetItems, "songset_id");
    expect(fk).toBeDefined();
    expect(getTableName(fk!.reference().foreignTable)).toBe("songsets");
  });

  it("renderJobs.songsetId references songsets.id", () => {
    const fk = findFkByColumnName(renderJobs, "songset_id");
    expect(fk).toBeDefined();
    expect(getTableName(fk!.reference().foreignTable)).toBe("songsets");
  });

  it("renderJobs.userId references users.id", () => {
    const fk = findFkByColumnName(renderJobs, "user_id");
    expect(fk).toBeDefined();
    expect(getTableName(fk!.reference().foreignTable)).toBe("user");
  });

  it("userLrcOverrides.userId references users.id", () => {
    const fk = findFkByColumnName(userLrcOverrides, "user_id");
    expect(fk).toBeDefined();
    expect(getTableName(fk!.reference().foreignTable)).toBe("user");
  });

  it("lyricMarks.recordingContentHash references recordings.contentHash", () => {
    const fk = findFkByColumnName(lyricMarks, "recording_content_hash");
    expect(fk).toBeDefined();
    expect(getTableName(fk!.reference().foreignTable)).toBe("recordings");
  });

  it("lyricsFeedback.recordingContentHash references recordings.contentHash", () => {
    const fk = findFkByColumnName(lyricsFeedback, "recording_content_hash");
    expect(fk).toBeDefined();
    expect(getTableName(fk!.reference().foreignTable)).toBe("recordings");
  });

  it("lyricsFeedback has open-feedback admin-queue index", () => {
    expect(indexNames(lyricsFeedback)).toContain("idx_lyrics_feedback_recording_resolved");
  });

  it("songEmbeddings.songId references songs.id", () => {
    const fk = findFkByColumnName(songEmbeddings, "song_id");
    expect(fk).toBeDefined();
    expect(getTableName(fk!.reference().foreignTable)).toBe("songs");
  });
});

describe("schema: page-load hot path indexes", () => {
  it("songsets has user updated index", () => {
    expect(indexNames(songsets)).toContain("idx_songsets_user_updated");
  });

  it("songsetItems has ordering and staleness indexes", () => {
    expect(indexNames(songsetItems)).toContain("idx_songset_items_songset_position");
    expect(indexNames(songsetItems)).toContain("idx_songset_items_songset_updated");
    expect(indexNames(songsetItems)).toContain("idx_songset_items_recording_hash_prefix");
  });

  it("renderJobs has songset and status indexes", () => {
    expect(indexNames(renderJobs)).toContain("idx_render_jobs_songset_created");
    expect(indexNames(renderJobs)).toContain("idx_render_jobs_status_updated");
  });
});

describe("schema: column defaults", () => {
  it("render_jobs status defaults to queued", () => {
    expect(renderJobs.status.default).toBe("queued");
  });

  it("render_jobs font_size_preset defaults to M", () => {
    expect(renderJobs.fontSizePreset.default).toBe("M");
  });

  it("render_jobs include_title_card defaults to false", () => {
    expect(renderJobs.includeTitleCard.default).toBe(false);
  });

  it("render_jobs template defaults to dark", () => {
    expect(renderJobs.template.default).toBe("dark");
  });

  it("render_jobs resolution defaults to 720p", () => {
    expect(renderJobs.resolution.default).toBe("720p");
  });

  it("songset_items gap_beats defaults to 2", () => {
    expect(songsetItems.gapBeats.default).toBe(2);
  });

  it("songEmbeddings model_version defaults to text-embedding-3-small", () => {
    expect(songEmbeddings.modelVersion.default).toBe("text-embedding-3-small");
  });

  it("songLineEmbeddings model_version defaults to text-embedding-3-small", () => {
    expect(songLineEmbeddings.modelVersion.default).toBe("text-embedding-3-small");
  });

  it("user_settings offline_auto_cache defaults to true", () => {
    expect(userSettings.offlineAutoCache.default).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Glue (AI transition) schema — ADR-0014 / spec §2.1
// ---------------------------------------------------------------------------

describe("schema: glue table names", () => {
  it("glueTakes maps to 'glue_takes'", () =>
    expect(getTableName(glueTakes)).toBe("glue_takes"));
  it("glueJobs maps to 'glue_jobs'", () => expect(getTableName(glueJobs)).toBe("glue_jobs"));
  it("gluePaidAttempts maps to 'glue_paid_attempts'", () =>
    expect(getTableName(gluePaidAttempts)).toBe("glue_paid_attempts"));
  it("glueExcerpts maps to 'glue_excerpts'", () =>
    expect(getTableName(glueExcerpts)).toBe("glue_excerpts"));
});

describe("schema: glue_takes structure", () => {
  it("has every immutable take column", () => {
    const cols = columnNames(glueTakes);
    expect(cols).toEqual(
      expect.arrayContaining([
        "id",
        "user_id",
        "descriptor_key",
        "descriptor_json",
        "ordinal",
        "seed",
        "status",
        "r2_key",
        "seconds",
        "raw_response_r2_key",
        "raw_response_sha256",
        "raw_response_bytes",
        "metrics_json",
        "reasons_json",
        "error_code",
        "created_at",
        "updated_at",
      ])
    );
  });

  it("uses text hashes/keys and an app-supplied uuid primary key", () => {
    expect(glueTakes.id.columnType).toBe("PgUUID");
    expect(glueTakes.id.hasDefault).toBe(false);
    expect(glueTakes.descriptorKey.columnType).toBe("PgText");
    expect(glueTakes.rawResponseSha256.columnType).toBe("PgText");
    expect(glueTakes.seconds.columnType).toBe("PgDoublePrecision");
    expect(glueTakes.seed.columnType).toBe("PgBigInt53");
    expect(glueTakes.rawResponseBytes.columnType).toBe("PgBigInt53");
  });

  it("defaults metrics/reasons JSON and requires descriptor text", () => {
    expect(glueTakes.metricsJson.default).toBe("{}");
    expect(glueTakes.reasonsJson.default).toBe("[]");
    expect(glueTakes.descriptorKey.notNull).toBe(true);
    expect(glueTakes.descriptorJson.notNull).toBe(true);
    expect(glueTakes.ordinal.notNull).toBe(true);
    expect(glueTakes.seed.notNull).toBe(true);
    expect(glueTakes.status.notNull).toBe(true);
  });

  it("has unique (user_id, descriptor_key, ordinal)", () => {
    const uniques = getTableConfig(glueTakes).uniqueConstraints;
    const cols = uniques.flatMap((u) => u.columns.map((c) => c.name));
    expect(cols).toEqual(
      expect.arrayContaining(["user_id", "descriptor_key", "ordinal"])
    );
  });

  it("has the descriptor lookup index", () => {
    expect(indexNames(glueTakes)).toContain("idx_glue_takes_user_descriptor_status_created");
  });

  it("user_id references users.id", () => {
    const fk = findFkByColumnName(glueTakes, "user_id");
    expect(fk).toBeDefined();
    expect(getTableName(fk!.reference().foreignTable)).toBe("user");
  });
});

describe("schema: glue_jobs structure", () => {
  it("has every job lifecycle column", () => {
    const cols = columnNames(glueJobs);
    expect(cols).toEqual(
      expect.arrayContaining([
        "id",
        "user_id",
        "songset_id",
        "from_item_id",
        "to_item_id",
        "client_request_id",
        "take_mode",
        "descriptor_key",
        "descriptor_json",
        "expected_from_updated_at",
        "expected_to_updated_at",
        "phase",
        "active",
        "dispatch_state",
        "dispatch_attempt",
        "attempt_id",
        "pending_take_id",
        "take_ordinal",
        "seed",
        "paid_reservation_id",
        "paid_reservation_state",
        "provider_contacted",
        "provider_phase",
        "provider_status",
        "provider_context_json",
        "retry_kind",
        "resulting_take_id",
        "active_take_id",
        "raw_response_r2_key",
        "raw_response_sha256",
        "raw_response_bytes",
        "error_code",
        "error_detail",
        "heartbeat_at",
        "created_at",
        "started_at",
        "updated_at",
        "finished_at",
      ])
    );
  });

  it("makes allocated take identity + attempt id non-null", () => {
    for (const column of [
      glueJobs.clientRequestId,
      glueJobs.attemptId,
      glueJobs.pendingTakeId,
      glueJobs.takeOrdinal,
      glueJobs.seed,
      glueJobs.expectedFromUpdatedAt,
      glueJobs.expectedToUpdatedAt,
      glueJobs.active,
      glueJobs.phase,
      glueJobs.dispatchState,
    ]) {
      expect(column.notNull).toBe(true);
    }
  });

  it("has job defaults", () => {
    expect(glueJobs.dispatchAttempt.default).toBe(0);
    expect(glueJobs.providerContacted.default).toBe(false);
  });

  it("has unique (user_id, client_request_id) for idempotency", () => {
    const uniques = getTableConfig(glueJobs).uniqueConstraints;
    const cols = uniques.flatMap((u) => u.columns.map((c) => c.name));
    expect(cols).toEqual(expect.arrayContaining(["user_id", "client_request_id"]));
  });

  it("pending_take_id is unique", () => {
    expect(glueJobs.pendingTakeId.isUnique).toBe(true);
  });

  it("has discovery and alert indexes", () => {
    expect(indexNames(glueJobs)).toEqual(
      expect.arrayContaining([
        "idx_glue_jobs_user_active_created",
        "idx_glue_jobs_phase_heartbeat",
        "idx_glue_jobs_songset_from_created",
      ])
    );
  });

  it("songset/from/to foreign keys target the owning rows", () => {
    const songsetFk = findFkByColumnName(glueJobs, "songset_id");
    expect(getTableName(songsetFk!.reference().foreignTable)).toBe("songsets");
    const fromFk = findFkByColumnName(glueJobs, "from_item_id");
    expect(getTableName(fromFk!.reference().foreignTable)).toBe("songset_items");
    const toFk = findFkByColumnName(glueJobs, "to_item_id");
    expect(getTableName(toFk!.reference().foreignTable)).toBe("songset_items");
    const userFk = findFkByColumnName(glueJobs, "user_id");
    expect(getTableName(userFk!.reference().foreignTable)).toBe("user");
  });

  it("resulting/active take ids reference glue_takes", () => {
    for (const col of ["resulting_take_id", "active_take_id"]) {
      const fk = findFkByColumnName(glueJobs, col);
      expect(fk).toBeDefined();
      expect(getTableName(fk!.reference().foreignTable)).toBe("glue_takes");
    }
  });
});

describe("schema: glue_paid_attempts structure", () => {
  it("has the quota-ledger columns", () => {
    const cols = columnNames(gluePaidAttempts);
    expect(cols).toEqual(
      expect.arrayContaining([
        "id",
        "user_id",
        "glue_job_id",
        "state",
        "consumed_at",
        "released_at",
        "created_at",
      ])
    );
  });

  it("glue_job_id is nullable and unique and clears when the job is deleted", () => {
    expect(gluePaidAttempts.glueJobId.notNull).toBe(false);
    expect(gluePaidAttempts.glueJobId.isUnique).toBe(true);
    const fk = findFkByColumnName(gluePaidAttempts, "glue_job_id");
    expect(fk).toBeDefined();
    expect(getTableName(fk!.reference().foreignTable)).toBe("glue_jobs");
    expect(fk!.onDelete).toBe("set null");
  });

  it("has the quota lookup index", () => {
    expect(indexNames(gluePaidAttempts)).toContain("idx_glue_paid_attempts_user_created");
  });
});

describe("schema: glue_excerpts structure", () => {
  it("has the cache columns", () => {
    const cols = columnNames(glueExcerpts);
    expect(cols).toEqual(
      expect.arrayContaining([
        "user_id",
        "logical_key",
        "version",
        "side",
        "seconds",
        "content_hash",
        "canonical_steps_json",
        "audio_sha256",
        "elevenlabs_song_id",
        "phase",
        "rejection_code",
        "safe_failure_detail",
        "created_at",
        "updated_at",
      ])
    );
  });

  it("version defaults to 2 and seconds is required", () => {
    expect(glueExcerpts.version.default).toBe(2);
    expect(glueExcerpts.seconds.notNull).toBe(true);
    expect(glueExcerpts.seconds.columnType).toBe("PgDoublePrecision");
  });

  it("has primary key (user_id, logical_key)", () => {
    const config = getTableConfig(glueExcerpts);
    const pkCols = config.primaryKeys.flatMap((pk) => pk.columns.map((c) => c.name));
    expect(pkCols).toEqual(expect.arrayContaining(["user_id", "logical_key"]));
  });

  it("user_id references users.id", () => {
    const fk = findFkByColumnName(glueExcerpts, "user_id");
    expect(fk).toBeDefined();
    expect(getTableName(fk!.reference().foreignTable)).toBe("user");
  });
});

describe("schema: glue additions to existing tables", () => {
  it("songset_items has glue enable/beats/active take columns", () => {
    const cols = columnNames(songsetItems);
    expect(cols).toEqual(
      expect.arrayContaining(["glue_enabled", "glue_beats", "glue_active_take_id"])
    );
  });

  it("songset_items glue defaults are off + 4 beats", () => {
    expect(songsetItems.glueEnabled.default).toBe(false);
    expect(songsetItems.glueEnabled.notNull).toBe(true);
    expect(songsetItems.glueBeats.default).toBe(4);
    expect(songsetItems.glueBeats.columnType).toBe("PgInteger");
  });

  it("songset_items.glue_active_take_id references glue_takes", () => {
    const fk = findFkByColumnName(songsetItems, "glue_active_take_id");
    expect(fk).toBeDefined();
    expect(getTableName(fk!.reference().foreignTable)).toBe("glue_takes");
    expect(fk!.onDelete).toBe("set null");
  });

  it("render_jobs has the nullable glue snapshot column", () => {
    expect(columnNames(renderJobs)).toContain("glue_transitions");
    expect(renderJobs.glueTransitions.columnType).toBe("PgText");
    expect(renderJobs.glueTransitions.notNull).toBe(false);
  });
});

describe("migration 0028: hand-written glue invariants", () => {
  const drizzleDir = path.resolve(__dirname, "../../../drizzle");
  const migrationFile = readdirSync(drizzleDir).find(
    (name) => name.startsWith("0028_") && name.endsWith(".sql")
  );
  const sql = migrationFile
    ? readFileSync(path.join(drizzleDir, migrationFile), "utf-8")
    : "";

  it("ships a 0028 migration alongside its snapshot and journal entry", () => {
    expect(migrationFile).toBe("0028_add_glue_tables.sql");
    const journal = JSON.parse(
      readFileSync(path.join(drizzleDir, "meta/_journal.json"), "utf-8")
    );
    expect(journal.entries).toContainEqual(
      expect.objectContaining({ idx: 28, tag: "0028_add_glue_tables" })
    );
    expect(() =>
      readFileSync(path.join(drizzleDir, "meta/0028_snapshot.json"), "utf-8")
    ).not.toThrow();
  });

  it("creates the partial unique active-job index exactly once", () => {
    expect(sql).toContain("CREATE UNIQUE INDEX IF NOT EXISTS uq_glue_jobs_active_per_user");
    expect(sql).toContain('ON "glue_jobs" ("user_id") WHERE "active"');
    const occurrences = sql.split("uq_glue_jobs_active_per_user").length - 1;
    expect(occurrences).toBe(1);
  });

  it("adds every phase/state CHECK from the contract", () => {
    for (const name of [
      "songset_items_glue_beats_check",
      "glue_jobs_active_phase_check",
      "glue_jobs_phase_check",
      "glue_jobs_take_mode_check",
      "glue_jobs_dispatch_state_check",
      "glue_jobs_reservation_state_check",
      "glue_jobs_retry_kind_check",
      "glue_takes_status_check",
      "glue_takes_ordinal_check",
      "glue_takes_ready_seconds_check",
      "glue_excerpts_side_check",
      "glue_excerpts_seconds_check",
      "glue_excerpts_phase_check",
      "glue_paid_attempts_state_check",
    ]) {
      expect(sql).toContain(`ADD CONSTRAINT "${name}"`);
    }
  });

  it("guards every hand-written constraint idempotently", () => {
    const guards = sql.split("SELECT 1 FROM pg_constraint").length - 1;
    expect(guards).toBe(14);
  });

  it("keeps the active-phase invariant literal", () => {
    expect(sql).toContain("active = (phase IN ('queued','preparing','uploading','composing','storing-response','staging'))");
  });

  it("emits each spec index exactly once (no duplicate DDL)", () => {
    for (const name of [
      "idx_glue_jobs_user_active_created",
      "idx_glue_jobs_phase_heartbeat",
      "idx_glue_jobs_songset_from_created",
      "idx_glue_paid_attempts_user_created",
      "idx_glue_takes_user_descriptor_status_created",
    ]) {
      expect(sql.split(name).length - 1).toBe(1);
    }
  });

  it("adds the beats check literal", () => {
    expect(sql).toContain("CHECK (glue_beats IN (2,4,8,16))");
  });
});
