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

const TRAILING_NUMBER_RE = /(\d+)\D*$/;

export function extractTrailingNumber(series: string | null): number | null {
  if (!series) return null;
  const match = stripAlbumSeriesParens(series).match(TRAILING_NUMBER_RE);
  return match ? parseInt(match[1], 10) : null;
}

export function extractSeriesPrefix(series: string | null): string | null {
  if (!series) return null;
  return stripAlbumSeriesParens(series).replace(TRAILING_NUMBER_RE, "").trim();
}

function compareNullsLast<T>(a: T | null, b: T | null, cmp: (a: T, b: T) => number): number {
  if (a === null && b === null) return 0;
  if (a === null) return 1;
  if (b === null) return -1;
  return cmp(a, b);
}

export function sortAlbumOptions(options: AlbumOption[]): AlbumOption[] {
  const mapped = options.map((opt) => ({
    opt,
    prefix: extractSeriesPrefix(opt.albumSeries),
    num: extractTrailingNumber(opt.albumSeries),
  }));

  mapped.sort((a, b) => {
    const prefixCmp = compareNullsLast(a.prefix, b.prefix, (x, y) => x.localeCompare(y));
    if (prefixCmp !== 0) return prefixCmp;

    const numCmp = compareNullsLast(a.num, b.num, (x, y) => x - y);
    if (numCmp !== 0) return numCmp;

    const seriesCmp = compareNullsLast(a.opt.albumSeries, b.opt.albumSeries, (x, y) =>
      x.localeCompare(y),
    );
    if (seriesCmp !== 0) return seriesCmp;

    return a.opt.albumName.localeCompare(b.opt.albumName);
  });

  return mapped.map((m) => m.opt);
}
