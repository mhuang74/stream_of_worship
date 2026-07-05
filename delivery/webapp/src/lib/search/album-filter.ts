export interface AlbumFilter {
  albumName: string;
  albumSeries: string | null;
}

export interface AlbumOption extends AlbumFilter {
  songCount: number;
}

export function albumFilterKey(album: AlbumFilter): string {
  return `${album.albumName}\u0000${album.albumSeries ?? ""}`;
}

const PAREN_CHARS = /[()（）]/g;

function stripAlbumSeriesParens(raw: string): string {
  return raw.replace(PAREN_CHARS, " ").replace(/\s+/g, " ").trim();
}

export function formatAlbumLabel(album: AlbumFilter): string {
  if (!album.albumSeries) return album.albumName;
  const series = stripAlbumSeriesParens(album.albumSeries);
  return series ? `${album.albumName} (${series})` : album.albumName;
}

export function formatAlbumOptionLabel(album: AlbumOption): string {
  return `${formatAlbumLabel(album)} [${album.songCount}]`;
}

export function normalizeAlbumFilters(values: AlbumFilter[]): AlbumFilter[] | undefined {
  const seen = new Set<string>();
  const albums: AlbumFilter[] = [];

  for (const value of values) {
    const albumName = value.albumName.trim();
    const albumSeries = value.albumSeries?.trim() || null;
    if (!albumName) continue;

    const normalized = { albumName, albumSeries };
    const key = albumFilterKey(normalized);
    if (seen.has(key)) continue;

    seen.add(key);
    albums.push(normalized);
    if (albums.length >= 25) break;
  }

  return albums.length > 0 ? albums : undefined;
}
