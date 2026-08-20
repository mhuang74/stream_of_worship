const STORAGE_KEY = "sow_songset_list_state";

interface SongsetListState {
  page: number;
  search: string;
}

/**
 * Persist the songsets list's current page + committed search so a
 * back-to-list navigation (editor back arrow, error states, delete success)
 * can reconstruct the exact URL the list had. No-op when storage is
 * unavailable (e.g. Safari private mode throws QuotaExceededError).
 */
export function saveSongsetListState(page: number, search: string): void {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ page, search }));
  } catch {
    // Private mode / disabled storage — list state simply isn't persisted.
  }
}

/**
 * Read the saved list state. Returns null on missing, corrupt, or invalid
 * data. `page` must be a finite integer >= 1; `search` must be a string.
 */
export function getSongsetListState(): SongsetListState | null {
  let raw: string | null;
  try {
    raw = sessionStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
  if (!raw) return null;

  try {
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null) return null;
    const { page, search } = parsed as Record<string, unknown>;
    if (
      typeof page !== "number" ||
      !Number.isFinite(page) ||
      !Number.isInteger(page) ||
      page < 1 ||
      typeof search !== "string"
    ) {
      return null;
    }
    return { page, search };
  } catch {
    return null;
  }
}

/**
 * Build the /songsets URL with the list's URL-shape convention:
 * `page` omitted when 1, `search` trimmed and omitted when empty.
 * Exported so the list's own URL-sync effect and the back-to-list helper
 * share one source of truth.
 */
export function buildSongsetsUrl(page: number, search: string): string {
  const params = new URLSearchParams();
  if (page > 1) params.set("page", String(page));
  const trimmed = search.trim();
  if (trimmed) params.set("search", trimmed);
  const qs = params.toString();
  return qs ? `/songsets?${qs}` : "/songsets";
}

/**
 * Build the /songsets URL from the saved list state. Matches the URL shape
 * SongsetsClient produces: `page` omitted when 1, `search` omitted when empty.
 * Returns bare `/songsets` when nothing is saved.
 */
export function songsetsListUrl(): string {
  const state = getSongsetListState();
  if (!state) return "/songsets";
  return buildSongsetsUrl(state.page, state.search);
}
