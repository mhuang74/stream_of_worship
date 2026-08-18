import type { SongWithRecordings } from "@/lib/db/songs";
import type { SongCardData } from "@/components/songset/SongCard";

/** Maps SongWithRecordings[] (DB shape, has Date objects) to the
 * serializable SongCardData[] shape used by client components. */
export function toSongCardData(songs: SongWithRecordings[]): SongCardData[] {
  return songs.map((song) => ({
    id: song.id,
    title: song.title,
    composer: song.composer,
    lyricist: song.lyricist,
    albumName: song.albumName,
    musicalKey: song.musicalKey,
    effectiveKey: song.effectiveKey,
    effectiveKeyStartRoot: song.effectiveKeyStartRoot,
    effectiveKeyEndRoot: song.effectiveKeyEndRoot,
    recordings: song.recordings.map((r) => ({
      contentHash: r.contentHash,
      hashPrefix: r.hashPrefix,
      durationSeconds: r.durationSeconds,
      tempoBpm: r.tempoBpm,
      musicalKey: r.musicalKey,
      effectiveKey: r.effectiveKey,
      effectiveKeyStartRoot: r.effectiveKeyStartRoot,
      effectiveKeyEndRoot: r.effectiveKeyEndRoot,
      visibilityStatus: r.visibilityStatus,
    })),
  }));
}
