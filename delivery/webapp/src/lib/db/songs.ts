import { db } from "@/db";
import { recordings, songs } from "@/db/schema";
import { eq, desc, and, or, ilike, sql, isNull, inArray } from "drizzle-orm";
import { getEffectiveKey, type EffectiveKey } from "@/lib/music/effective-key";
import { parseMusicalKey } from "@/lib/music/key";
import {
  buildEffectiveKeyPredicate,
  buildBpmPredicate,
  buildVisibilityCondition,
} from "./search-helpers";
import type { BpmBandKey } from "@/lib/constants";
import type { AlbumFilter, AlbumOption } from "@/lib/search/album-filter";

export interface SongWithRecordings {
  id: string;
  title: string;
  titlePinyin: string | null;
  composer: string | null;
  lyricist: string | null;
  albumName: string | null;
  albumSeries: string | null;
  musicalKey: string | null;
  effectiveKey: string | null;
  effectiveKeySource: EffectiveKey["source"];
  effectiveKeyStartRoot: string | null;
  effectiveKeyEndRoot: string | null;
  effectiveKeyMode: EffectiveKey["mode"];
  effectiveKeyStartPitchClass: number | null;
  effectiveKeyEndPitchClass: number | null;
  keyWarning: EffectiveKey["warning"];
  createdAt: Date | null;
  updatedAt: Date | null;
  recordings: RecordingInfo[];
}

export interface RecordingInfo {
  contentHash: string;
  hashPrefix: string;
  originalFilename: string;
  durationSeconds: number | null;
  tempoBpm: number | null;
  musicalKey: string | null;
  musicalMode: string | null;
  keyConfidence: number | null;
  keyScoreMargin: number | null;
  keyWindowAgreement: number | null;
  keyAlgorithmVersion: string | null;
  effectiveKey: string | null;
  effectiveKeySource: EffectiveKey["source"];
  effectiveKeyStartRoot: string | null;
  effectiveKeyEndRoot: string | null;
  effectiveKeyMode: EffectiveKey["mode"];
  effectiveKeyStartPitchClass: number | null;
  effectiveKeyEndPitchClass: number | null;
  keyWarning: EffectiveKey["warning"];
  loudnessDb: number | null;
  r2AudioUrl: string | null;
  r2LrcUrl: string | null;
  visibilityStatus: string | null;
  analysisStatus: string | null;
}

type SongRow = typeof songs.$inferSelect & {
  recordings: Array<typeof recordings.$inferSelect>;
};

function effectiveKeyFields(effective: EffectiveKey) {
  return {
    effectiveKey: effective.display,
    effectiveKeySource: effective.source,
    effectiveKeyStartRoot: effective.startRoot,
    effectiveKeyEndRoot: effective.endRoot,
    effectiveKeyMode: effective.mode,
    effectiveKeyStartPitchClass: effective.startPitchClass,
    effectiveKeyEndPitchClass: effective.endPitchClass,
    keyWarning: effective.warning,
  };
}

function mapRecordingInfo(
  song: typeof songs.$inferSelect,
  recording: typeof recordings.$inferSelect
): RecordingInfo {
  const effective = getEffectiveKey({
    catalogKey: song.musicalKey,
    catalogParsed: parseMusicalKey(song.musicalKey),
    detectedKey: recording.musicalKey,
    detectedMode: recording.musicalMode,
    detectedConfidence: recording.keyConfidence,
    detectedMargin: recording.keyScoreMargin,
    detectedWindowAgreement: recording.keyWindowAgreement,
  });
  return {
    contentHash: recording.contentHash,
    hashPrefix: recording.hashPrefix,
    originalFilename: recording.originalFilename,
    durationSeconds: recording.durationSeconds,
    tempoBpm: recording.tempoBpm,
    musicalKey: recording.musicalKey,
    musicalMode: recording.musicalMode,
    keyConfidence: recording.keyConfidence,
    keyScoreMargin: recording.keyScoreMargin,
    keyWindowAgreement: recording.keyWindowAgreement,
    keyAlgorithmVersion: recording.keyAlgorithmVersion,
    ...effectiveKeyFields(effective),
    loudnessDb: recording.loudnessDb,
    r2AudioUrl: recording.r2AudioUrl,
    r2LrcUrl: recording.r2LrcUrl,
    visibilityStatus: recording.visibilityStatus,
    analysisStatus: recording.analysisStatus,
  };
}

export function mapSongWithRecordings(song: SongRow): SongWithRecordings {
  const primaryRecording =
    song.recordings.find((recording) => recording.visibilityStatus === "published") ??
    song.recordings[0];
  const effective = getEffectiveKey({
    catalogKey: song.musicalKey,
    catalogParsed: parseMusicalKey(song.musicalKey),
    detectedKey: primaryRecording?.musicalKey,
    detectedMode: primaryRecording?.musicalMode,
    detectedConfidence: primaryRecording?.keyConfidence,
    detectedMargin: primaryRecording?.keyScoreMargin,
    detectedWindowAgreement: primaryRecording?.keyWindowAgreement,
  });

  return {
    id: song.id,
    title: song.title,
    titlePinyin: song.titlePinyin,
    composer: song.composer,
    lyricist: song.lyricist,
    albumName: song.albumName,
    albumSeries: song.albumSeries,
    musicalKey: song.musicalKey,
    ...effectiveKeyFields(effective),
    createdAt: song.createdAt,
    updatedAt: song.updatedAt,
    recordings: song.recordings.map((recording) => mapRecordingInfo(song, recording)),
  };
}

export interface SongDetail extends SongWithRecordings {
  lyricsRaw: string | null;
  lyricsLines: string | null;
  sections: string | null;
  sourceUrl: string;
}

export interface ListSongsFilters {
  albumName?: string;
  albumNames?: string[];
  albumFilters?: AlbumFilter[];
  albumSeries?: string;
  composer?: string;
  lyricist?: string;
  visibilityStatus?: string | string[];
  keys?: string[];
  bpmRange?: BpmBandKey;
}

function buildPublishedRecordingExistsClause(
  visibilityStatus?: string | string[]
) {
  if (!visibilityStatus || visibilityStatus === "all") {
    return undefined;
  }

  if (Array.isArray(visibilityStatus)) {
    if (visibilityStatus.length === 0) return undefined;
    return sql`exists (
      select 1
      from recordings
      where recordings.song_id = ${songs.id}
        and recordings.visibility_status = ANY(${sql`ARRAY[${sql.join(visibilityStatus.map(s => sql`${s}`), sql`, `)}]::text[]`})
        and recordings.deleted_at IS NULL
    )`;
  }

  return sql`exists (
    select 1
    from recordings
    where recordings.song_id = ${songs.id}
      and recordings.visibility_status = ${visibilityStatus}
      and recordings.deleted_at IS NULL
  )`;
}

function recordingVisibilityPredicate(
  visibilityStatus?: string | string[],
  recordingsAlias = recordings
) {
  if (!visibilityStatus || visibilityStatus === "all") return undefined;
  if (Array.isArray(visibilityStatus)) {
    if (visibilityStatus.length === 0) return undefined;
    return inArray(recordingsAlias.visibilityStatus, visibilityStatus);
  }
  return eq(recordingsAlias.visibilityStatus, visibilityStatus);
}

function buildSongWhereClause(
  filters?: ListSongsFilters,
  query?: string
) {
  const whereConditions = [];

  whereConditions.push(isNull(songs.deletedAt));

  if (query) {
    const searchTerm = `%${query}%`;
    whereConditions.push(
      or(
        ilike(songs.title, searchTerm),
        ilike(songs.composer, searchTerm),
        ilike(songs.lyricist, searchTerm),
        ilike(songs.albumName, searchTerm)
      )
    );
  }

  if (filters?.albumName) {
    whereConditions.push(eq(songs.albumName, filters.albumName));
  }
  if (filters?.albumNames?.length) {
    whereConditions.push(inArray(songs.albumName, filters.albumNames));
  }
  if (filters?.albumFilters?.length) {
    whereConditions.push(
      or(
        ...filters.albumFilters.map((album) =>
          and(
            eq(songs.albumName, album.albumName),
            album.albumSeries === null
              ? isNull(songs.albumSeries)
              : eq(songs.albumSeries, album.albumSeries)
          )
        )
      )
    );
  }
  if (filters?.albumSeries) {
    whereConditions.push(eq(songs.albumSeries, filters.albumSeries));
  }
  if (filters?.composer) {
    whereConditions.push(eq(songs.composer, filters.composer));
  }
  if (filters?.lyricist) {
    whereConditions.push(eq(songs.lyricist, filters.lyricist));
  }

  if (filters?.keys && (filters.keys?.length ?? 0) > 0) {
    const visCond = buildVisibilityCondition(filters?.visibilityStatus, "r2");
    whereConditions.push(
      sql`exists (
        select 1 from recordings r2
        where r2.song_id = ${songs.id}
          and r2.deleted_at IS NULL
          ${visCond ? sql`and ${visCond}` : sql``}
          and ${buildEffectiveKeyPredicate(filters.keys, "songs", "r2")}
      )`
    );
  }

  if (filters?.bpmRange) {
    const bpmPredicate = buildBpmPredicate(filters.bpmRange, "r3");
    const visCond = buildVisibilityCondition(filters?.visibilityStatus, "r3");
    whereConditions.push(
      sql`exists (
        select 1 from recordings r3
        where r3.song_id = ${songs.id}
          and r3.deleted_at IS NULL
          and r3.tempo_bpm IS NOT NULL
          ${visCond ? sql`and ${visCond}` : sql``}
          and ${bpmPredicate}
      )`
    );
  }

  const publishedRecordingsClause = buildPublishedRecordingExistsClause(
    filters?.visibilityStatus
  );
  if (publishedRecordingsClause) {
    whereConditions.push(publishedRecordingsClause);
  }

  return whereConditions.length > 0 ? and(...whereConditions) : undefined;
}

export async function listSongs(
  limit: number = 50,
  offset: number = 0,
  filters?: ListSongsFilters
): Promise<{ songs: SongWithRecordings[]; total: number }> {
  const whereClause = buildSongWhereClause(filters);
  const recordingWhereConditions = [];
  const visPredicate = recordingVisibilityPredicate(filters?.visibilityStatus);
  if (visPredicate) recordingWhereConditions.push(visPredicate);
  recordingWhereConditions.push(isNull(recordings.deletedAt));
  const recordingWhereClause = recordingWhereConditions.length > 0
    ? and(...recordingWhereConditions)
    : undefined;

  const result = await db.query.songs.findMany({
    where: whereClause,
    orderBy: [desc(songs.updatedAt)],
    limit,
    offset,
    with: {
      recordings: recordingWhereClause
        ? { where: recordingWhereClause }
        : true,
    },
  });

  const countResult = await db
    .select({ count: sql<number>`count(*)` })
    .from(songs)
    .where(whereClause ?? sql`true`);

  const total = countResult[0]?.count ?? 0;

  const songsWithRecordings = result.map(mapSongWithRecordings);

  return { songs: songsWithRecordings, total };
}

export async function getSong(
  id: string,
  visibilityStatus: string = "published"
): Promise<SongDetail | null> {
  const visibilityWhereClause = buildSongWhereClause({ visibilityStatus });
  const recordingWhereConditions = [];
  if (visibilityStatus !== "all") {
    recordingWhereConditions.push(eq(recordings.visibilityStatus, visibilityStatus));
  }
  recordingWhereConditions.push(isNull(recordings.deletedAt));
  const recordingWhereClause = recordingWhereConditions.length > 0
    ? and(...recordingWhereConditions)
    : undefined;
  const song = await db.query.songs.findFirst({
    where: visibilityWhereClause
      ? and(eq(songs.id, id), visibilityWhereClause)
      : eq(songs.id, id),
    with: {
      recordings: recordingWhereClause
        ? { where: recordingWhereClause }
        : true,
    },
  });

  if (!song) {
    return null;
  }

  return {
    ...mapSongWithRecordings(song),
    lyricsRaw: song.lyricsRaw,
    lyricsLines: song.lyricsLines,
    sections: song.sections,
    sourceUrl: song.sourceUrl,
  };
}

export interface SearchSongsResult {
  songs: SongWithRecordings[];
  total: number;
}

export async function searchSongs(
  query: string,
  limit: number = 50,
  offset: number = 0,
  visibilityStatus?: string | string[]
): Promise<SearchSongsResult> {
  const whereClause = buildSongWhereClause({ visibilityStatus }, query);
  const recordingWhereConditions = [];
  const visPredicate = recordingVisibilityPredicate(visibilityStatus);
  if (visPredicate) recordingWhereConditions.push(visPredicate);
  recordingWhereConditions.push(isNull(recordings.deletedAt));
  const recordingWhereClause = recordingWhereConditions.length > 0
    ? and(...recordingWhereConditions)
    : undefined;

  const result = await db.query.songs.findMany({
    where: whereClause,
    orderBy: [desc(songs.updatedAt)],
    limit,
    offset,
    with: {
      recordings: recordingWhereClause
        ? { where: recordingWhereClause }
        : true,
    },
  });

  const countResult = await db
    .select({ count: sql<number>`count(*)` })
    .from(songs)
    .where(whereClause);

  const total = countResult[0]?.count ?? 0;

  const songsWithRecordings = result.map(mapSongWithRecordings);

  return { songs: songsWithRecordings, total };
}

export async function getAlbums(): Promise<AlbumOption[]> {
  const result = await db
    .select({
      albumName: songs.albumName,
      albumSeries: songs.albumSeries,
      songCount: sql<number>`count(*)`,
    })
    .from(songs)
    .where(sql`${songs.albumName} IS NOT NULL
      AND exists (
        select 1 from recordings
        where recordings.song_id = ${songs.id}
          and recordings.visibility_status IN ('published', 'review')
          and recordings.deleted_at IS NULL
      )`)
    .groupBy(songs.albumName, songs.albumSeries)
    .orderBy(songs.albumName, songs.albumSeries);

  return result
    .filter((row): row is typeof row & { albumName: string } => row.albumName !== null)
    .map((row) => ({
      albumName: row.albumName,
      albumSeries: row.albumSeries,
      songCount: Number(row.songCount),
    }));
}

export interface SemanticSearchResult extends SongWithRecordings {
  similarity: number;
  modelVersion: string;
  matchingSnippet: string | null;
  whyThisMatch: string[];
  rrfScore?: number;
}

export interface SemanticSearchOptions {
  albums?: string[];
  albumFilters?: AlbumFilter[];
  keys?: string[];
  bpmRange?: BpmBandKey;
}

function validateEmbedding(embedding: number[], expectedDims: number = 1536): string {
  if (embedding.length !== expectedDims) {
    throw new Error(`Invalid embedding: expected ${expectedDims} dimensions, got ${embedding.length}`);
  }
  for (const v of embedding) {
    if (typeof v !== "number" || !isFinite(v)) {
      throw new Error("Invalid embedding value: all values must be finite numbers");
    }
    if (Math.abs(v) > 100) {
      throw new Error("Invalid embedding value: values must be in range [-100, 100]");
    }
  }
  return "[" + embedding.join(",") + "]";
}

export async function semanticSearchSongs(
  embedding: number[],
  expectedModelVersion: string,
  limit: number = 20,
  visibilityStatuses: string[] = ["published", "review"],
  options?: SemanticSearchOptions,
): Promise<SemanticSearchResult[]> {
  const vectorStr = validateEmbedding(embedding);
  const albumFilter = options?.albumFilters?.length
    ? sql`AND (${sql.join(
        options.albumFilters.map((album) =>
          album.albumSeries === null
            ? sql`(s.album_name = ${album.albumName} AND s.album_series IS NULL)`
            : sql`(s.album_name = ${album.albumName} AND s.album_series = ${album.albumSeries})`
        ),
        sql` OR `
      )})`
    : options?.albums?.length
    ? sql`AND s.album_name = ANY(${sql`ARRAY[${sql.join(options.albums.map(a => sql`${a}`), sql`, `)}]::text[]`})`
    : sql``;
  const keyFilter = options?.keys?.length
    ? sql`AND ${buildEffectiveKeyPredicate(options.keys, "s", "r")}`
    : sql``;
  const bpmFilter = options?.bpmRange
    ? sql`AND ${buildBpmPredicate(options.bpmRange, "r")}`
    : sql``;

  const rows = await db.execute(sql`
    SELECT * FROM (
      SELECT DISTINCT ON (s.id)
        s.id,
        s.title,
        s.title_pinyin,
        s.composer,
        s.lyricist,
        s.album_name,
        s.album_series,
        s.musical_key,
        s.created_at,
        s.updated_at,
        r.content_hash,
        r.hash_prefix,
        r.original_filename,
        r.duration_seconds,
        r.tempo_bpm,
        r.musical_key  AS recording_musical_key,
        r.musical_mode,
        r.key_confidence,
        r.key_score_margin,
        r.key_window_agreement,
        r.key_algorithm_version,
        r.loudness_db,
        r.r2_audio_url,
        r.r2_lrc_url,
        r.visibility_status,
        r.analysis_status,
        se.model_version,
        (1 - (se.embedding <=> ${vectorStr}::vector))::float AS similarity
      FROM song_embedding se
      JOIN songs s ON se.song_id = s.id
      JOIN recordings r ON r.song_id = s.id
        AND r.visibility_status = ANY(${sql`ARRAY[${sql.join(visibilityStatuses.map(s => sql`${s}`), sql`, `)}]::text[]`})
        AND r.deleted_at IS NULL
        ${keyFilter}
        ${bpmFilter}
      WHERE s.deleted_at IS NULL
        AND se.model_version = ${expectedModelVersion}
        ${albumFilter}
      ORDER BY s.id, se.embedding <=> ${vectorStr}::vector ASC
    ) ranked
    ORDER BY similarity DESC
    LIMIT ${limit}
  `);

  const resultRows = rows.rows as unknown as Record<string, unknown>[];

  return resultRows.map((row) => {
    const effective = getEffectiveKey({
      catalogKey: (row.musical_key as string | null) ?? null,
      detectedKey: (row.recording_musical_key as string | null) ?? null,
      detectedMode: (row.musical_mode as string | null) ?? null,
      detectedConfidence: (row.key_confidence as number | null) ?? null,
      detectedMargin: (row.key_score_margin as number | null) ?? null,
      detectedWindowAgreement: (row.key_window_agreement as number | null) ?? null,
    });
    const fields = effectiveKeyFields(effective);
    return {
      id: row.id as string,
      title: row.title as string,
      titlePinyin: (row.title_pinyin as string | null) ?? null,
      composer: (row.composer as string | null) ?? null,
      lyricist: (row.lyricist as string | null) ?? null,
      albumName: (row.album_name as string | null) ?? null,
      albumSeries: (row.album_series as string | null) ?? null,
      musicalKey: (row.musical_key as string | null) ?? null,
      ...fields,
      createdAt: row.created_at ? new Date(row.created_at as string) : null,
      updatedAt: row.updated_at ? new Date(row.updated_at as string) : null,
      similarity: Number(row.similarity),
      modelVersion: row.model_version as string,
      matchingSnippet: null,
      whyThisMatch: [],
      recordings: [
        {
          contentHash: row.content_hash as string,
          hashPrefix: row.hash_prefix as string,
          originalFilename: row.original_filename as string,
          durationSeconds: (row.duration_seconds as number | null) ?? null,
          tempoBpm: (row.tempo_bpm as number | null) ?? null,
          musicalKey: (row.recording_musical_key as string | null) ?? null,
          musicalMode: (row.musical_mode as string | null) ?? null,
          keyConfidence: (row.key_confidence as number | null) ?? null,
          keyScoreMargin: (row.key_score_margin as number | null) ?? null,
          keyWindowAgreement: (row.key_window_agreement as number | null) ?? null,
          keyAlgorithmVersion: (row.key_algorithm_version as string | null) ?? null,
          ...fields,
          loudnessDb: (row.loudness_db as number | null) ?? null,
          r2AudioUrl: (row.r2_audio_url as string | null) ?? null,
          r2LrcUrl: (row.r2_lrc_url as string | null) ?? null,
          visibilityStatus: (row.visibility_status as string | null) ?? null,
          analysisStatus: (row.analysis_status as string | null) ?? null,
        },
      ],
    };
  });
}

export async function findTopMatchingLines(
  queryEmbedding: number[],
  songIds: string[]
): Promise<Map<string, { lineText: string; lineSimilarity: number }[]>> {
  if (songIds.length === 0) return new Map();

  const vectorStr = validateEmbedding(queryEmbedding);

  const rows = await db.execute(sql`
    SELECT song_id, line_text, line_similarity
    FROM (
      SELECT
        sle.song_id,
        sle.line_text,
        (1 - (sle.embedding <=> ${vectorStr}::vector))::float AS line_similarity,
        ROW_NUMBER() OVER (
          PARTITION BY sle.song_id
          ORDER BY sle.embedding <=> ${vectorStr}::vector ASC
        ) AS rn
      FROM song_line_embedding sle
      WHERE sle.song_id = ANY(ARRAY[${sql.join(songIds.map(id => sql`${id}`), sql`, `)}]::text[])
        AND length(regexp_replace(sle.line_text, '[^\u4e00-\u9fff]', '', 'g')) >= 4
    ) ranked
    WHERE rn <= 2
    ORDER BY song_id, rn
  `);

  const resultRows = rows.rows as unknown as Record<string, unknown>[];
  const result = new Map<string, { lineText: string; lineSimilarity: number }[]>();
  for (const row of resultRows) {
    const songId = row.song_id as string;
    const lines = result.get(songId) ?? [];
    lines.push({
      lineText: row.line_text as string,
      lineSimilarity: Number(row.line_similarity),
    });
    result.set(songId, lines);
  }
  return result;
}

const RRF_K = 60;
const MIN_LINE_COVERAGE = 0.5;

export function rrfRerank(
  songs: SemanticSearchResult[],
  snippets: Map<string, { lineText: string; lineSimilarity: number }[]>,
  k: number = RRF_K,
): SemanticSearchResult[] {
  if (songs.length === 0) return songs;

  const maxLineSimBySong = new Map<string, number>();
  let songsWithLines = 0;
  for (const song of songs) {
    const lines = snippets.get(song.id);
    if (lines && lines.length > 0) {
      maxLineSimBySong.set(song.id, lines[0].lineSimilarity);
      songsWithLines++;
    } else {
      maxLineSimBySong.set(song.id, 0);
    }
  }

  if (songsWithLines / songs.length < MIN_LINE_COVERAGE) {
    return songs;
  }

  const rankSong = new Map<string, number>();
  songs.forEach((song, i) => rankSong.set(song.id, i + 1));

  const rankLine = new Map<string, number>();
  const songsByLineSim = [...songs].sort((a, b) =>
    (maxLineSimBySong.get(b.id) ?? 0) - (maxLineSimBySong.get(a.id) ?? 0),
  );
  songsByLineSim.forEach((song, i) => rankLine.set(song.id, i + 1));

  const lastRank = songs.length + 1;
  const reranked = songs.map((song) => ({
    ...song,
    rrfScore: 1 / (k + (rankSong.get(song.id) ?? lastRank))
            + 1 / (k + (rankLine.get(song.id) ?? lastRank)),
  }));

  reranked.sort((a, b) => b.rrfScore - a.rrfScore);
  return reranked;
}
