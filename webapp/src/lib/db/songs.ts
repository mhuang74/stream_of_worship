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
  createdAt: Date;
  updatedAt: Date;
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
