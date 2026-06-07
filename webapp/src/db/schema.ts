import {
  pgTable,
  text,
  integer,
  serial,
  real,
  boolean,
  bigint,
  doublePrecision,
  timestamp,
  index,
  unique,
  vector,
  customType,
} from "drizzle-orm/pg-core";
import { relations } from "drizzle-orm";
import { sql } from "drizzle-orm";

const tsvector = customType<{ data: string; driverData: string }>({
  dataType() {
    return "tsvector";
  },
  toDriver(value: string) {
    return value;
  },
  fromDriver(value: string) {
    return value;
  },
});

// ---------------------------------------------------------------------------
// Catalog tables (managed by admin CLI, read-only from webapp)
// ---------------------------------------------------------------------------

export const songs = pgTable(
  "songs",
  {
    id: text("id").primaryKey(),
    title: text("title").notNull(),
    titlePinyin: text("title_pinyin"),
    composer: text("composer"),
    lyricist: text("lyricist"),
    albumName: text("album_name"),
    albumSeries: text("album_series"),
    musicalKey: text("musical_key"),
    lyricsRaw: text("lyrics_raw"),
    lyricsLines: text("lyrics_lines"),
    sections: text("sections"),
    sourceUrl: text("source_url").notNull(),
    tableRowNumber: integer("table_row_number"),
    scrapedAt: text("scraped_at").notNull(),
    createdAt: timestamp("created_at", { withTimezone: true }).defaultNow(),
    updatedAt: timestamp("updated_at", { withTimezone: true }).defaultNow(),
    deletedAt: timestamp("deleted_at", { withTimezone: true }),
    searchVector: tsvector("search_vector").generatedAlwaysAs(
      sql`setweight(to_tsvector('simple', coalesce("title", '')), 'A') ||
          setweight(to_tsvector('simple', coalesce("title_pinyin", '')), 'A') ||
          setweight(to_tsvector('simple', coalesce("composer", '')), 'B') ||
          setweight(to_tsvector('simple', coalesce("lyricist", '')), 'B') ||
          setweight(to_tsvector('simple', coalesce("album_name", '')), 'B')`
    ),
  },
  (t) => [index("idx_songs_search_vector").on(t.searchVector)]
);

export const recordings = pgTable("recordings", {
  contentHash: text("content_hash").primaryKey(),
  hashPrefix: text("hash_prefix").notNull().unique(),
  songId: text("song_id").references(() => songs.id),
  originalFilename: text("original_filename").notNull(),
  fileSizeBytes: integer("file_size_bytes").notNull(),
  importedAt: text("imported_at").notNull(),

  r2AudioUrl: text("r2_audio_url"),
  r2StemsUrl: text("r2_stems_url"),
  r2LrcUrl: text("r2_lrc_url"),

  durationSeconds: real("duration_seconds"),
  tempoBpm: real("tempo_bpm"),
  musicalKey: text("musical_key"),
  musicalMode: text("musical_mode"),
  keyConfidence: real("key_confidence"),
  loudnessDb: real("loudness_db"),
  beats: text("beats"),
  downbeats: text("downbeats"),
  sections: text("sections"),
  embeddingsShape: text("embeddings_shape"),

  analysisStatus: text("analysis_status").default("pending"),
  analysisJobId: text("analysis_job_id"),
  lrcStatus: text("lrc_status").default("pending"),
  lrcJobId: text("lrc_job_id"),

  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow(),
  updatedAt: timestamp("updated_at", { withTimezone: true }).defaultNow(),

  youtubeUrl: text("youtube_url"),
  visibilityStatus: text("visibility_status"),
  downloadStatus: text("download_status").default("pending"),
  deletedAt: timestamp("deleted_at", { withTimezone: true }),
});

// ---------------------------------------------------------------------------
// Better Auth tables (camelCase SQL column names to match Better Auth schema)
// useNumberId: true → BIGINT GENERATED ALWAYS AS IDENTITY
// ---------------------------------------------------------------------------

export const users = pgTable("user", {
  id: bigint("id", { mode: "number" }).generatedAlwaysAsIdentity().primaryKey(),
  name: text("name").notNull(),
  email: text("email").notNull().unique(),
  emailVerified: boolean("emailVerified").notNull().default(false),
  image: text("image"),
  createdAt: timestamp("createdAt", { withTimezone: true }).notNull().defaultNow(),
  updatedAt: timestamp("updatedAt", { withTimezone: true }).notNull().defaultNow(),
});

export const accounts = pgTable(
  "account",
  {
    id: bigint("id", { mode: "number" }).generatedAlwaysAsIdentity().primaryKey(),
    userId: bigint("userId", { mode: "number" })
      .notNull()
      .references(() => users.id, { onDelete: "cascade" }),
    accountId: text("accountId").notNull(),
    providerId: text("providerId").notNull(),
    accessToken: text("accessToken"),
    refreshToken: text("refreshToken"),
    idToken: text("idToken"),
    accessTokenExpiresAt: timestamp("accessTokenExpiresAt", { withTimezone: true }),
    refreshTokenExpiresAt: timestamp("refreshTokenExpiresAt", { withTimezone: true }),
    scope: text("scope"),
    password: text("password"),
    createdAt: timestamp("createdAt", { withTimezone: true }).notNull().defaultNow(),
    updatedAt: timestamp("updatedAt", { withTimezone: true }).notNull().defaultNow(),
  },
  (t) => [unique().on(t.providerId, t.accountId)]
);

export const sessions = pgTable("session", {
  id: bigint("id", { mode: "number" }).generatedAlwaysAsIdentity().primaryKey(),
  userId: bigint("userId", { mode: "number" })
    .notNull()
    .references(() => users.id, { onDelete: "cascade" }),
  token: text("token").notNull().unique(),
  expiresAt: timestamp("expiresAt", { withTimezone: true }).notNull(),
  ipAddress: text("ipAddress"),
  userAgent: text("userAgent"),
  createdAt: timestamp("createdAt", { withTimezone: true }).notNull().defaultNow(),
  updatedAt: timestamp("updatedAt", { withTimezone: true }).notNull().defaultNow(),
});

export const verifications = pgTable("verification", {
  id: bigint("id", { mode: "number" }).generatedAlwaysAsIdentity().primaryKey(),
  identifier: text("identifier").notNull(),
  value: text("value").notNull(),
  expiresAt: timestamp("expiresAt", { withTimezone: true }).notNull(),
  createdAt: timestamp("createdAt", { withTimezone: true }).notNull().defaultNow(),
  updatedAt: timestamp("updatedAt", { withTimezone: true }).notNull().defaultNow(),
});

// ---------------------------------------------------------------------------
// App tables: songsets and songset_items
// ---------------------------------------------------------------------------

export const songsets = pgTable("songsets", {
  id: text("id").primaryKey(),
  userId: bigint("user_id", { mode: "number" })
    .notNull()
    .references(() => users.id, { onDelete: "cascade" }),
  name: text("name").notNull(),
  description: text("description"),
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow().notNull(),
  updatedAt: timestamp("updated_at", { withTimezone: true }).defaultNow().notNull(),

  // Render tracking
  latestRenderJobId: text("latest_render_job_id"),
  lastFailedRenderJobId: text("last_failed_render_job_id"),
  lastCompletedRenderJobId: text("last_completed_render_job_id"),
}, (t) => [
  index("idx_songsets_user_updated").on(t.userId, t.updatedAt),
]);

export const songsetItems = pgTable("songset_items", {
  id: text("id").primaryKey(),
  songsetId: text("songset_id")
    .notNull()
    .references(() => songsets.id, { onDelete: "cascade" }),
  songId: text("song_id").notNull(),
  recordingHashPrefix: text("recording_hash_prefix"),
  position: integer("position").notNull(),

  // Transition parameters (null values apply to first song)
  gapBeats: real("gap_beats").default(2.0),
  crossfadeEnabled: integer("crossfade_enabled").default(0),
  crossfadeDurationSeconds: real("crossfade_duration_seconds"),
  keyShiftSemitones: integer("key_shift_semitones").default(0),
  tempoRatio: real("tempo_ratio").default(1.0),

  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow().notNull(),
  updatedAt: timestamp("updated_at", { withTimezone: true }).defaultNow().notNull(),
}, (t) => [
  index("idx_songset_items_songset_position").on(t.songsetId, t.position),
  index("idx_songset_items_songset_updated").on(t.songsetId, t.updatedAt),
]);

// ---------------------------------------------------------------------------
// New webapp table: render_jobs
// ---------------------------------------------------------------------------

export const renderJobs = pgTable("render_jobs", {
  id: text("id").primaryKey(),
  songsetId: text("songset_id")
    .notNull()
    .references(() => songsets.id, { onDelete: "cascade" }),
  userId: bigint("user_id", { mode: "number" })
    .notNull()
    .references(() => users.id, { onDelete: "cascade" }),

  // Status tracking
  status: text("status").notNull().default("queued"),
  phase: text("phase"),
  phaseIndex: integer("phase_index"),
  totalPhases: integer("total_phases"),
  /** @deprecated No longer written by pipeline. Retained for historical data only. */
  percentComplete: real("percent_complete").default(0),
  /** @deprecated No longer written by pipeline. Retained for historical data only. */
  estimatedSecondsLeft: real("estimated_seconds_left"),
  elapsedSeconds: real("elapsed_seconds"),
  errorMessage: text("error_message"),
  estimatedTotalSeconds: real("estimated_total_seconds"),
  totalDurationSeconds: real("total_duration_seconds"),
  startedAt: timestamp("started_at", { withTimezone: true }),

  // Render options
  template: text("template").notNull().default("dark"),
  resolution: text("resolution").notNull().default("720p"),
  audioEnabled: boolean("audio_enabled").notNull().default(true),
  videoEnabled: boolean("video_enabled").notNull().default(true),

  // Font and title card options (plan-specified columns)
  fontSizePreset: text("font_size_preset").notNull().default("M"),
  fontFamily: text("font_family").notNull().default("noto_serif_tc"),
  includeTitleCard: boolean("include_title_card").notNull().default(false),
  titleCardDurationSeconds: real("title_card_duration_seconds").default(10),
  titleCardLines: text("title_card_lines"),

  // Snapshot columns (populated at render creation time)
  songCount: integer("song_count"),
  songsetDurationSeconds: integer("songset_duration_seconds"),

  // Output R2 keys (set when render completes)
  mp3R2Key: text("mp3_r2_key"),
  mp4R2Key: text("mp4_r2_key"),
  chaptersR2Key: text("chapters_r2_key"),

  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow(),
  updatedAt: timestamp("updated_at", { withTimezone: true }).defaultNow(),
  completedAt: timestamp("completed_at", { withTimezone: true }),
}, (t) => [
  index("idx_render_jobs_songset_created").on(t.songsetId, t.createdAt),
  index("idx_render_jobs_status_updated").on(t.status, t.updatedAt),
]);

// ---------------------------------------------------------------------------
// New webapp table: song_embedding (pgvector for semantic search)
// Keyed by song_id per spec v4; embedding content is title+composer+lyrics_raw
// ---------------------------------------------------------------------------

export const songEmbeddings = pgTable(
  "song_embedding",
  {
    songId: text("song_id")
      .primaryKey()
      .references(() => songs.id, { onDelete: "cascade" }),
    embedding: vector("embedding", { dimensions: 1536 }).notNull(),
    modelVersion: text("model_version")
      .notNull()
      .default("text-embedding-3-small"),
    contentHash: text("content_hash").notNull(),
    createdAt: timestamp("created_at", { withTimezone: true }).defaultNow(),
  },
  (t) => [
    index("idx_song_embedding_cosine").on(
      sql`${t.embedding} vector_cosine_ops`
    ),
  ]
);

// ---------------------------------------------------------------------------
// New webapp table: song_line_embedding (pgvector for snippet matching)
// Pre-computed during batch pipeline; eliminates query-time OpenAI call
// ---------------------------------------------------------------------------

export const songLineEmbeddings = pgTable(
  "song_line_embedding",
  {
    id: serial("id").primaryKey(),
    songId: text("song_id")
      .notNull()
      .references(() => songs.id, { onDelete: "cascade" }),
    lineIndex: integer("line_index").notNull(),
    lineText: text("line_text").notNull(),
    embedding: vector("embedding", { dimensions: 1536 }).notNull(),
    modelVersion: text("model_version")
      .notNull()
      .default("text-embedding-3-small"),
  },
  (t) => [
    index("idx_song_line_embedding_song").on(t.songId),
    index("idx_song_line_embedding_cosine").on(
      sql`${t.embedding} vector_cosine_ops`
    ),
  ]
);

// ---------------------------------------------------------------------------
// Per-user data tables (matching existing Python schema table names)
// ---------------------------------------------------------------------------

export const userSettings = pgTable("user_settings", {
  userId: bigint("user_id", { mode: "number" })
    .primaryKey()
    .references(() => users.id, { onDelete: "cascade" }),
  offlineAutoCache: boolean("offline_auto_cache").notNull().default(true),
  defaultGapBeats: real("default_gap_beats").notNull().default(2.0),
  defaultVideoTemplate: text("default_video_template").notNull().default("dark"),
  defaultResolution: text("default_resolution").notNull().default("720p"),
  lyricsLoopWindowSeconds: real("lyrics_loop_window_seconds").notNull().default(3.0),
  defaultFontSizePreset: text("default_font_size_preset").notNull().default("M"),
  defaultFontFamily: text("default_font_family").notNull().default("noto_serif_tc"),
  defaultKeyShiftSemitones: integer("default_key_shift_semitones").notNull().default(0),
  timingReviewFont: text("timing_review_font").notNull().default("sans"),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
  updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().defaultNow(),
});

// user_lrc_overrides: per-user LRC text overrides (table: user_lrc_override)
export const userLrcOverrides = pgTable(
  "user_lrc_override",
  {
    id: text("id").primaryKey(),
    userId: bigint("user_id", { mode: "number" })
      .notNull()
      .references(() => users.id, { onDelete: "cascade" }),
    recordingContentHash: text("recording_content_hash")
      .notNull()
      .references(() => recordings.contentHash, { onDelete: "cascade" }),
    lrcContent: text("lrc_content").notNull(),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
    updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (t) => [unique().on(t.userId, t.recordingContentHash)]
);

// lyric_marks: per-user problem line markers (table: lyric_mark)
export const lyricMarks = pgTable(
  "lyric_mark",
  {
    id: text("id").primaryKey(),
    userId: bigint("user_id", { mode: "number" })
      .notNull()
      .references(() => users.id, { onDelete: "cascade" }),
    recordingContentHash: text("recording_content_hash")
      .notNull()
      .references(() => recordings.contentHash, { onDelete: "cascade" }),
    timestampSeconds: doublePrecision("timestamp_seconds").notNull(),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (t) => [unique().on(t.userId, t.recordingContentHash, t.timestampSeconds)]
);

// songset_shares: share tokens for songsets (table: songset_share)
export const songsetShares = pgTable("songset_share", {
  token: text("token").primaryKey(),
  songsetId: text("songset_id")
    .notNull()
    .references(() => songsets.id, { onDelete: "cascade" }),
  renderJobId: text("render_job_id"),
  createdByUserId: bigint("created_by_user_id", { mode: "number" })
    .notNull()
    .references(() => users.id, { onDelete: "cascade" }),
  allowDownload: boolean("allow_download").notNull().default(false),
  expiresAt: timestamp("expires_at", { withTimezone: true }),
  revokedAt: timestamp("revoked_at", { withTimezone: true }),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
});

// ---------------------------------------------------------------------------
// Relations
// ---------------------------------------------------------------------------

export const songsRelations = relations(songs, ({ many }) => ({
  recordings: many(recordings),
  songEmbeddings: many(songEmbeddings),
  songLineEmbeddings: many(songLineEmbeddings),
}));

export const recordingsRelations = relations(recordings, ({ one, many }) => ({
  song: one(songs, { fields: [recordings.songId], references: [songs.id] }),
  userLrcOverrides: many(userLrcOverrides),
  lyricMarks: many(lyricMarks),
}));

export const usersRelations = relations(users, ({ one, many }) => ({
  accounts: many(accounts),
  sessions: many(sessions),
  songsets: many(songsets),
  renderJobs: many(renderJobs),
  userSettings: one(userSettings, { fields: [users.id], references: [userSettings.userId] }),
  userLrcOverrides: many(userLrcOverrides),
  lyricMarks: many(lyricMarks),
  songsetShares: many(songsetShares),
}));

export const accountsRelations = relations(accounts, ({ one }) => ({
  user: one(users, { fields: [accounts.userId], references: [users.id] }),
}));

export const sessionsRelations = relations(sessions, ({ one }) => ({
  user: one(users, { fields: [sessions.userId], references: [users.id] }),
}));

export const songsetsRelations = relations(songsets, ({ one, many }) => ({
  user: one(users, { fields: [songsets.userId], references: [users.id] }),
  items: many(songsetItems),
  renderJobs: many(renderJobs),
  shares: many(songsetShares),
}));

export const songsetItemsRelations = relations(songsetItems, ({ one }) => ({
  songset: one(songsets, { fields: [songsetItems.songsetId], references: [songsets.id] }),
  song: one(songs, { fields: [songsetItems.songId], references: [songs.id] }),
  recording: one(recordings, {
    fields: [songsetItems.recordingHashPrefix],
    references: [recordings.hashPrefix],
  }),
}));

export const renderJobsRelations = relations(renderJobs, ({ one }) => ({
  songset: one(songsets, { fields: [renderJobs.songsetId], references: [songsets.id] }),
  user: one(users, { fields: [renderJobs.userId], references: [users.id] }),
}));

export const songEmbeddingsRelations = relations(songEmbeddings, ({ one }) => ({
  song: one(songs, {
    fields: [songEmbeddings.songId],
    references: [songs.id],
  }),
}));

export const songLineEmbeddingsRelations = relations(
  songLineEmbeddings,
  ({ one }) => ({
    song: one(songs, {
      fields: [songLineEmbeddings.songId],
      references: [songs.id],
    }),
  })
);

export const userSettingsRelations = relations(userSettings, ({ one }) => ({
  user: one(users, { fields: [userSettings.userId], references: [users.id] }),
}));

export const userLrcOverridesRelations = relations(userLrcOverrides, ({ one }) => ({
  user: one(users, { fields: [userLrcOverrides.userId], references: [users.id] }),
  recording: one(recordings, {
    fields: [userLrcOverrides.recordingContentHash],
    references: [recordings.contentHash],
  }),
}));

export const lyricMarksRelations = relations(lyricMarks, ({ one }) => ({
  user: one(users, { fields: [lyricMarks.userId], references: [users.id] }),
  recording: one(recordings, {
    fields: [lyricMarks.recordingContentHash],
    references: [recordings.contentHash],
  }),
}));

export const songsetSharesRelations = relations(songsetShares, ({ one }) => ({
  songset: one(songsets, { fields: [songsetShares.songsetId], references: [songsets.id] }),
  createdByUser: one(users, {
    fields: [songsetShares.createdByUserId],
    references: [users.id],
  }),
}));
