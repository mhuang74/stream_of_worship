import { db } from "@/db";
import { songs } from "@/db/schema";
import { eq, desc, and, or, ilike, sql } from "drizzle-orm";

export interface SongWithRecordings {
  id: string;
  title: string;
  titlePinyin: string | null;
  composer: string | null;
  lyricist: string | null;
  albumName: string | null;
  albumSeries: string | null;
  musicalKey: string | null;
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
  loudnessDb: number | null;
  r2AudioUrl: string | null;
  r2LrcUrl: string | null;
  visibilityStatus: string | null;
  analysisStatus: string | null;
}

export interface SongDetail extends SongWithRecordings {
  lyricsRaw: string | null;
  lyricsLines: string | null;
  sections: string | null;
  sourceUrl: string;
}

export interface ListSongsFilters {
  albumName?: string;
  albumSeries?: string;
  composer?: string;
  lyricist?: string;
  visibilityStatus?: string;
}

export async function listSongs(
  limit: number = 50,
  offset: number = 0,
  filters?: ListSongsFilters
): Promise<{ songs: SongWithRecordings[]; total: number }> {
  // Build where clause with filters
  const whereConditions = [];

  if (filters?.albumName) {
    whereConditions.push(eq(songs.albumName, filters.albumName));
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

  const whereClause = whereConditions.length > 0 ? and(...whereConditions) : undefined;

  // Get songs with recordings
  const result = await db.query.songs.findMany({
    where: whereClause,
    orderBy: [desc(songs.updatedAt)],
    limit,
    offset,
    with: {
      recordings: true,
    },
  });

  // Filter recordings by visibility_status if specified
  const filteredResult = result.map((song) => {
    let filteredRecordings = song.recordings;
    if (filters?.visibilityStatus) {
      filteredRecordings = song.recordings.filter(
        (r) => r.visibilityStatus === filters.visibilityStatus
      );
    }
    return {
      ...song,
      recordings: filteredRecordings,
    };
  });

  // Get total count
  const countResult = await db
    .select({ count: sql<number>`count(*)` })
    .from(songs)
    .where(whereClause ?? sql`true`);

  const total = countResult[0]?.count ?? 0;

  const songsWithRecordings: SongWithRecordings[] = filteredResult.map((song) => ({
    id: song.id,
    title: song.title,
    titlePinyin: song.titlePinyin,
    composer: song.composer,
    lyricist: song.lyricist,
    albumName: song.albumName,
    albumSeries: song.albumSeries,
    musicalKey: song.musicalKey,
    createdAt: song.createdAt,
    updatedAt: song.updatedAt,
    recordings: song.recordings.map((r) => ({
      contentHash: r.contentHash,
      hashPrefix: r.hashPrefix,
      originalFilename: r.originalFilename,
      durationSeconds: r.durationSeconds,
      tempoBpm: r.tempoBpm,
      musicalKey: r.musicalKey,
      musicalMode: r.musicalMode,
      loudnessDb: r.loudnessDb,
      r2AudioUrl: r.r2AudioUrl,
      r2LrcUrl: r.r2LrcUrl,
      visibilityStatus: r.visibilityStatus,
      analysisStatus: r.analysisStatus,
    })),
  }));

  return { songs: songsWithRecordings, total };
}

export async function getSong(id: string): Promise<SongDetail | null> {
  const song = await db.query.songs.findFirst({
    where: eq(songs.id, id),
    with: {
      recordings: true,
    },
  });

  if (!song) {
    return null;
  }

  return {
    id: song.id,
    title: song.title,
    titlePinyin: song.titlePinyin,
    composer: song.composer,
    lyricist: song.lyricist,
    albumName: song.albumName,
    albumSeries: song.albumSeries,
    musicalKey: song.musicalKey,
    lyricsRaw: song.lyricsRaw,
    lyricsLines: song.lyricsLines,
    sections: song.sections,
    sourceUrl: song.sourceUrl,
    createdAt: song.createdAt,
    updatedAt: song.updatedAt,
    recordings: song.recordings.map((r) => ({
      contentHash: r.contentHash,
      hashPrefix: r.hashPrefix,
      originalFilename: r.originalFilename,
      durationSeconds: r.durationSeconds,
      tempoBpm: r.tempoBpm,
      musicalKey: r.musicalKey,
      musicalMode: r.musicalMode,
      loudnessDb: r.loudnessDb,
      r2AudioUrl: r.r2AudioUrl,
      r2LrcUrl: r.r2LrcUrl,
      visibilityStatus: r.visibilityStatus,
      analysisStatus: r.analysisStatus,
    })),
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
  visibilityStatus?: string
): Promise<SearchSongsResult> {
  const searchTerm = `%${query}%`;

  // Search in songs table
  const whereConditions = [
    or(
      ilike(songs.title, searchTerm),
      ilike(songs.composer, searchTerm),
      ilike(songs.lyricist, searchTerm),
      ilike(songs.albumName, searchTerm)
    ),
  ];

  const whereClause = and(...whereConditions);

  const result = await db.query.songs.findMany({
    where: whereClause,
    orderBy: [desc(songs.updatedAt)],
    limit,
    offset,
    with: {
      recordings: true,
    },
  });

  // Filter recordings by visibility_status if specified
  const filteredResult = result.map((song) => {
    let filteredRecordings = song.recordings;
    if (visibilityStatus) {
      filteredRecordings = song.recordings.filter(
        (r) => r.visibilityStatus === visibilityStatus
      );
    }
    return {
      ...song,
      recordings: filteredRecordings,
    };
  });

  // Get total count
  const countResult = await db
    .select({ count: sql<number>`count(*)` })
    .from(songs)
    .where(whereClause);

  const total = countResult[0]?.count ?? 0;

  const songsWithRecordings: SongWithRecordings[] = filteredResult.map((song) => ({
    id: song.id,
    title: song.title,
    titlePinyin: song.titlePinyin,
    composer: song.composer,
    lyricist: song.lyricist,
    albumName: song.albumName,
    albumSeries: song.albumSeries,
    musicalKey: song.musicalKey,
    createdAt: song.createdAt,
    updatedAt: song.updatedAt,
    recordings: song.recordings.map((r) => ({
      contentHash: r.contentHash,
      hashPrefix: r.hashPrefix,
      originalFilename: r.originalFilename,
      durationSeconds: r.durationSeconds,
      tempoBpm: r.tempoBpm,
      musicalKey: r.musicalKey,
      musicalMode: r.musicalMode,
      loudnessDb: r.loudnessDb,
      r2AudioUrl: r.r2AudioUrl,
      r2LrcUrl: r.r2LrcUrl,
      visibilityStatus: r.visibilityStatus,
      analysisStatus: r.analysisStatus,
    })),
  }));

  return { songs: songsWithRecordings, total };
}

export async function getAlbums(): Promise<string[]> {
  const result = await db
    .selectDistinct({ albumName: songs.albumName })
    .from(songs)
    .where(sql`${songs.albumName} IS NOT NULL`)
    .orderBy(songs.albumName);

  return result.map((r) => r.albumName).filter((name): name is string => name !== null);
}

export async function getComposers(): Promise<string[]> {
  const result = await db
    .selectDistinct({ composer: songs.composer })
    .from(songs)
    .where(sql`${songs.composer} IS NOT NULL`)
    .orderBy(songs.composer);

  return result.map((r) => r.composer).filter((name): name is string => name !== null);
}

export interface SemanticSearchResult extends SongWithRecordings {
  similarity: number;
}

export async function semanticSearchSongs(
  embedding: number[],
  limit: number = 20
): Promise<SemanticSearchResult[]> {
  const vectorStr = `[${embedding.join(",")}]`;

  // For each song, pick the recording with the best similarity, then rank all songs by that score
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
        r.loudness_db,
        r.r2_audio_url,
        r.r2_lrc_url,
        r.visibility_status,
        r.analysis_status,
        (1 - (se.embedding <=> ${vectorStr}::vector))::float AS similarity
      FROM song_embedding se
      JOIN recordings r ON se.recording_content_hash = r.content_hash
      JOIN songs s ON r.song_id = s.id
      WHERE r.visibility_status = 'published'
      ORDER BY s.id, se.embedding <=> ${vectorStr}::vector ASC
    ) ranked
    ORDER BY similarity DESC
    LIMIT ${limit}
  `);

  return (rows as Record<string, unknown>[]).map((row) => ({
    id: row.id as string,
    title: row.title as string,
    titlePinyin: (row.title_pinyin as string | null) ?? null,
    composer: (row.composer as string | null) ?? null,
    lyricist: (row.lyricist as string | null) ?? null,
    albumName: (row.album_name as string | null) ?? null,
    albumSeries: (row.album_series as string | null) ?? null,
    musicalKey: (row.musical_key as string | null) ?? null,
    createdAt: row.created_at ? new Date(row.created_at as string) : null,
    updatedAt: row.updated_at ? new Date(row.updated_at as string) : null,
    similarity: Number(row.similarity),
    recordings: [
      {
        contentHash: row.content_hash as string,
        hashPrefix: row.hash_prefix as string,
        originalFilename: row.original_filename as string,
        durationSeconds: (row.duration_seconds as number | null) ?? null,
        tempoBpm: (row.tempo_bpm as number | null) ?? null,
        musicalKey: (row.recording_musical_key as string | null) ?? null,
        musicalMode: (row.musical_mode as string | null) ?? null,
        loudnessDb: (row.loudness_db as number | null) ?? null,
        r2AudioUrl: (row.r2_audio_url as string | null) ?? null,
        r2LrcUrl: (row.r2_lrc_url as string | null) ?? null,
        visibilityStatus: (row.visibility_status as string | null) ?? null,
        analysisStatus: (row.analysis_status as string | null) ?? null,
      },
    ],
  }));
}
