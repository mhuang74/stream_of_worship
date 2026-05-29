import { describe, it, expect } from "vitest";
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
  songsetShares,
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

  it("songEmbeddings.songId references songs.id", () => {
    const fk = findFkByColumnName(songEmbeddings, "song_id");
    expect(fk).toBeDefined();
    expect(getTableName(fk!.reference().foreignTable)).toBe("songs");
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

  it("songEmbeddings model_version defaults to openai-text-embedding-3-small", () => {
    expect(songEmbeddings.modelVersion.default).toBe("openai-text-embedding-3-small");
  });

  it("songLineEmbeddings model_version defaults to openai-text-embedding-3-small", () => {
    expect(songLineEmbeddings.modelVersion.default).toBe("openai-text-embedding-3-small");
  });

  it("user_settings offline_auto_cache defaults to true", () => {
    expect(userSettings.offlineAutoCache.default).toBe(true);
  });
});
