---

# Webapp Player: Find Songsets Containing Currently Playing Song (v2)

## Summary

This revision corrects and strengthens the [original plan](./webapp-player-find-containing-songsets.md). It keeps the core feature — a "locate" button in the player bar that shows all songsets containing the currently playing song and navigates to the chosen songset with the target song highlighted — while fixing type-safety, query correctness, SSR, accessibility, focus, security, and operational issues found during review.

The user clarified the following design choices:

1. **Highlight state is ephemeral only**: `?highlightSong=` is consumed on arrival and immediately removed from the URL/view state; it is not bookmarkable.
2. **Design for future sharing**: The reverse-lookup API response shape should include an `owner` field so "shared with me" songsets can be listed later without breaking the API contract.
3. **Fetch on every open**: The popover fetches containing songsets each time it is opened to reflect recent edits; caching is out of scope.

---

## Problem

When a user plays a song from Songset A, then navigates away (e.g., to browse Songset C or the library), they lose track of which songsets contain the currently loaded song. There is no quick way to:

- return to the originating songset,
- discover other sets that contain the same song, or
- jump directly to the song's position inside any of those sets.

## Decisions

| Topic | Decision |
|-------|----------|
| Scope listing | Show the originating songset first, then all other songsets containing the song. |
| UI entry point | Icon button in the player bar's track-info area; opens a popover listing containing songsets. |
| Navigate action | Navigate to `/songsets/[id]?highlightSong=[songId]`; detail page scrolls to and temporarily highlights the specific song, then cleans the param. |
| Track type scope | Button shows only for `"song"` tracks; hidden for `"transition"` and `"lyrics-loop"`. |
| Sharing future-proofing | API response shape includes an `owner` field for shared songsets. |
| Data freshness | Re-fetch every time the popover opens; no client caching. |
| URL highlight | Ephemeral: consumed in client state and stripped from URL. |

## Issues Found in the Original Plan

### 1. Type safety — `AudioTrack.id` prefix is not a parse contract

The original plan relied on `currentTrack.id` containing `song-${songId}` and proposed extracting `songId` from it. `AudioTrack` is a player-specific type; the `id` is opaque and could change format. The robust fix is to store `songId` as a first-class structured field on `AudioTrack`.

### 2. Missing `originSongsetId` on `AudioTrack`

The player has no way to know which songset started playback. The plan correctly adds `originSongsetId`, but the original `PlaySongOptions` definition and call sites under-specified it.

### 3. DB query in the spec was invalid (duplicate alias and broken self-join)

The sample query aliased `songsetItems` twice without using Drizzle's `alias()` helper, which would fail at runtime. The item-count aggregation also required a subquery or separate group-by strategy.

### 4. No index on `songsetItems.songId`

`songId` is queried by value for the reverse lookup, but the table only indexes `songsetId` and `recordingHashPrefix`. A new index on `songId` (or `songId, songsetId`) is required to avoid a full table scan.

### 5. API route params typing mismatch

The original used `{ params: Promise<{ id: string }> }` inline in the route signature. The codebase uses a named `RouteParams` interface for consistency (see `app/api/songs/[id]/route.ts`).

### 6. Missing validation / input sanitization

`songId` from the URL and `origin` query string must be validated (e.g., with Zod or at least non-empty checks) before hitting the DB.

### 7. Highlight param handling risked scroll-before-render

Waiting a fixed 300 ms is unreliable with drag-and-drop layouts, virtualization, or slow hydration. The plan should instead wait for layout via `requestAnimationFrame` and retry briefly, or gate the scroll until items are present in the DOM.

### 8. URL cleanup could erase other query params

`router.replace(`/songsets/${songsetId}`)` strips `?new=true`, `?share=true`, and future params. The implementation must clean only the `highlightSong` param while preserving the rest.

### 9. "Origin" badge ambiguity when no origin is known

When a song is played from Browse or Semantic Search with no `originSongsetId`, nothing should be marked "Origin". The original text conflated this with "no containing songsets"; this plan separates the two cases.

### 10. Accessibility / keyboard

The original popover used a list of plain `<button>` elements without focus management. The popover should be keyboard navigable, include `aria-label`, and return focus to the trigger on close.

### 11. Operational / observability

No mention of error logging, rate limiting, or `timePageLoad` instrumentation despite all other DB helpers using it.

---

## Implementation Plan (v2)

### Phase 1: Enrich player types and call sites

#### 1a. Extend `AudioTrack`

File: `delivery/webapp/src/contexts/AudioPlayerContext.tsx`

Add optional `songId` and `originSongsetId`:

```typescript
export interface AudioTrack {
  id: string;
  title: string;
  artist: string;
  src: string;
  type: AudioTrackType;
  duration?: number;
  loopStart?: number;
  loopEnd?: number;
  recordingContentHash?: string;
  /** Set for type "song" tracks so the player bar can identify the source song. */
  songId?: string;
  /** The songset from which playback was initiated, if any. */
  originSongsetId?: string;
}
```

#### 1b. Extend `PlaySongOptions` and `playSong`

File: `delivery/webapp/src/hooks/useAudioPlayer.ts`

```typescript
interface PlaySongOptions {
  songId: string;
  title: string;
  artist: string;
  src: string;
  duration?: number;
  recordingContentHash?: string;
  originSongsetId?: string;
}

const playSong = useCallback((options: PlaySongOptions) => {
  const track: AudioTrack = {
    id: `song-${options.songId}`,
    title: options.title,
    artist: options.artist,
    src: options.src,
    type: "song",
    duration: options.duration,
    recordingContentHash: options.recordingContentHash,
    songId: options.songId,
    originSongsetId: options.originSongsetId,
  };
  play(track);
}, [play]);
```

#### 1c. Thread origin through existing call sites

File: `delivery/webapp/src/components/songset/SongList.tsx`

Ensure `SongList` receives `songsetId` from `SongsetEditor` and passes it into `play()`. Important: `SongList` currently calls the **context-level** `play` directly, not `useAudioPlayer().playSong`. The implementation can either:

- Option A (preferred): keep `SongList` calling context `play` and add `songId`/`originSongsetId` to the two `play()` calls in `handlePlaySong`.
- Option B: switch `SongList` to use the `useAudioPlayer` hook's `playSong`, threading `originSongsetId` through `PlaySongOptions`.

This plan uses **Option A** to avoid unnecessary refactoring:

```typescript
play({
  id: `song-${songId}`,
  title: item.song?.title ?? "Unknown Song",
  artist,
  src: publicUrl,
  type: "song",
  duration: recording.durationSeconds ?? undefined,
  recordingContentHash: recording.contentHash,
  songId: songId,
  originSongsetId: songsetId,
});
```

Repeat the same fields in the second `play()` call inside the signed-url fallback.

File: `delivery/webapp/src/app/songsets/[id]/SongsetEditorClient.tsx`

Pass `songsetId` to `SongsetEditor` so it can be forwarded to `SongList`.

Files: `BrowseSheet.tsx`, `SemanticSearch.tsx`, other song-list play sites  
Call `playSong` (or `play`) with `songId` but **omit** `originSongsetId`.

> Do **not** set `originSongsetId` to any fabricated value. When there is no originating songset, it stays absent.

---

### Phase 2: Database schema and reverse lookup

#### 2a. Add index on `songsetItems.songId`

File: `delivery/webapp/src/db/schema.ts`

Add inside the `songsetItems` table configuration:

```typescript
index("idx_songset_items_song_id").on(t.songId),
```

Run:

```bash
cd delivery/webapp && npx drizzle-kit generate
# or, in dev:
npx drizzle-kit push
```

#### 2b. Implement `findSongsetsContainingSong`

File: `delivery/webapp/src/lib/db/songsets.ts`

Use a subquery/CTE for item count so the aggregation remains correct without fragile self-joins:

```typescript
export interface SongsetContainingSong {
  id: string;
  name: string;
  description: string | null;
  updatedAt: Date;
  itemCount: number;
  songPosition: number;
  isOrigin: boolean;
  owner: {
    id: number;
    name: string;
  };
}

export async function findSongsetsContainingSong(
  songId: string,
  userId: number,
  originSongsetId?: string | null
): Promise<SongsetContainingSong[]> {
  return timePageLoad("findSongsetsContainingSong", async () => {
    // If a song appears multiple times in one songset, this returns one row per occurrence.
    const itemCountSubquery = db
      .$with("item_counts")
      .as(
        db
          .select({
            songsetId: songsetItems.songsetId,
            count: sql<number>`count(${songsetItems.id})::int`.as("count"),
          })
          .from(songsetItems)
          .groupBy(songsetItems.songsetId)
      );

    const rows = await db
      .with(itemCountSubquery)
      .select({
        id: songsets.id,
        name: songsets.name,
        description: songsets.description,
        updatedAt: songsets.updatedAt,
        itemCount: itemCountSubquery.count,
        songPosition: songsetItems.position,
        ownerId: users.id,
        ownerName: users.name,
      })
      .from(songsets)
      .innerJoin(songsetItems, eq(songsetItems.songsetId, songsets.id))
      .innerJoin(users, eq(users.id, songsets.userId))
      .leftJoin(itemCountSubquery, eq(itemCountSubquery.songsetId, songsets.id))
      .where(
        and(
          eq(songsets.userId, userId),
          eq(songsetItems.songId, songId)
        )
      )
      .orderBy(desc(songsets.updatedAt));

    // Put origin first, then updatedAt desc.
    const sorted = [...rows].sort((a, b) => {
      if (originSongsetId) {
        if (a.id === originSongsetId && b.id !== originSongsetId) return -1;
        if (b.id === originSongsetId && a.id !== originSongsetId) return 1;
      }
      return b.updatedAt.getTime() - a.updatedAt.getTime();
    });

    return sorted.map((row) => ({
      id: row.id,
      name: row.name,
      description: row.description,
      updatedAt: row.updatedAt,
      itemCount: Number(row.itemCount ?? 0),
      songPosition: row.songPosition,
      isOrigin: row.id === originSongsetId,
      owner: {
        id: row.ownerId,
        name: row.ownerName ?? "Unknown",
      },
    }));
  });
}
```

If the same song appears multiple times inside one songset, the result will contain one row per occurrence, each with the correct `songPosition`. The UI can display duplicate rows or be deduplicated later (out of scope for this plan).

#### 2c. Add API route

File: `delivery/webapp/src/app/api/songs/[id]/songsets/route.ts` (NEW)

```typescript
import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { findSongsetsContainingSong } from "@/lib/db/songsets";

interface RouteParams {
  params: Promise<{ id: string }>;
}

export async function GET(request: NextRequest, { params }: RouteParams) {
  try {
    const session = await auth.api.getSession({ headers: request.headers });
    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const { id: rawSongId } = await params;
    const songId = rawSongId.trim();
    if (!songId) {
      return NextResponse.json({ error: "Invalid song id" }, { status: 400 });
    }

    const rawOrigin = request.nextUrl.searchParams.get("origin");
    const originSongsetId = rawOrigin?.trim() || null;

    const songsets = await findSongsetsContainingSong(
      songId,
      Number(session.user.id),
      originSongsetId
    );

    return NextResponse.json({ songsets });
  } catch (error) {
    console.error("Error finding containing songsets:", error);
    return NextResponse.json(
      { error: "Failed to find containing songsets" },
      { status: 500 }
    );
  }
}
```

API contract:  
`GET /api/songs/:songId/songsets?origin=<songsetId>`

Response fields include `owner` for future sharing support.

---

### Phase 3: Player bar popover UI

#### 3a. Create `LocateSongsetsPopover`

File: `delivery/webapp/src/components/audio/LocateSongsetsPopover.tsx` (NEW)

Key behavioral changes from the original plan:

- Fetch every open (no caching).
- Use an accessible list with roving/focusable items.
- Return focus to trigger on close.
- Distinguish the "no origin" state from the "no containing songsets" state.

```tsx
"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useAudioPlayer } from "@/hooks/useAudioPlayer";
import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import { MapPin, Loader2, ListMusic, ArrowUpRight } from "lucide-react";

interface ContainingSongset {
  id: string;
  name: string;
  description: string | null;
  updatedAt: string;
  itemCount: number;
  songPosition: number;
  isOrigin: boolean;
  owner: { id: number; name: string };
}

export function LocateSongsetsPopover() {
  const { currentTrack } = useAudioPlayer();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [songsets, setSongsets] = useState<ContainingSongset[]>([]);
  const [error, setError] = useState<string | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  const songId = currentTrack?.type === "song" ? currentTrack.songId : undefined;
  const originSongsetId = currentTrack?.originSongsetId;

  useEffect(() => {
    if (!open || !songId) return;

    setLoading(true);
    setError(null);
    const params = new URLSearchParams();
    if (originSongsetId) params.set("origin", originSongsetId);

    fetch(`/api/songs/${encodeURIComponent(songId)}/songsets?${params.toString()}`)
      .then(async (res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        setSongsets(data.songsets ?? []);
      })
      .catch((err) => {
        console.error("Locate songsets failed:", err);
        setError("Failed to load songsets");
      })
      .finally(() => setLoading(false));
  }, [open, songId, originSongsetId]);

  const handleSelect = useCallback(
    (songsetId: string) => {
      if (!songId) return;
      router.push(
        `/songsets/${songsetId}?highlightSong=${encodeURIComponent(songId)}`
      );
      setOpen(false);
    },
    [router, songId]
  );

  // Return focus to trigger when closed.
  useEffect(() => {
    if (!open && triggerRef.current) {
      triggerRef.current.focus();
    }
  }, [open]);

  if (!songId) return null;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          ref={triggerRef}
          variant="ghost"
          size="icon-sm"
          className="shrink-0"
          aria-label="Find containing songsets"
          title="Find in songsets"
        >
          <MapPin className="size-4" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-72 p-0" align="start">
        {loading ? (
          <div className="flex items-center justify-center py-6">
            <Loader2 className="size-5 animate-spin text-muted-foreground" />
          </div>
        ) : error ? (
          <div className="px-3 py-4 text-sm text-destructive" role="alert">
            {error}
          </div>
        ) : songsets.length === 0 ? (
          <div className="px-3 py-4 text-sm text-muted-foreground">
            This song is not in any of your songsets.
          </div>
        ) : (
          <div
            role="listbox"
            aria-label="Songsets containing this song"
            className="flex flex-col max-h-64 overflow-y-auto"
          >
            {songsets.map((ss) => (
              <button
                key={`${ss.id}-${ss.songPosition}`}
                role="option"
                tabIndex={0}
                onClick={() => handleSelect(ss.id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    handleSelect(ss.id);
                  }
                }}
                className={cn(
                  "flex items-center gap-3 px-3 py-2 text-left hover:bg-accent focus:bg-accent transition-colors border-b last:border-b-0"
                )}
              >
                <ListMusic className="size-4 text-muted-foreground shrink-0" />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium truncate">{ss.name}</p>
                  <p className="text-xs text-muted-foreground">
                    {ss.itemCount} songs • Position {ss.songPosition + 1}
                  </p>
                </div>
                {ss.isOrigin && (
                  <span className="text-xs text-primary shrink-0 font-medium">
                    Origin
                  </span>
                )}
                <ArrowUpRight className="size-3.5 text-muted-foreground shrink-0" />
              </button>
            ))}
          </div>
        )}
      </PopoverContent>
    </Popover>
  );
}
```

Optional: use a popover primitive that supports arrow-key navigation, or add a lightweight roving-keyboard hook within the component.

#### 3b. Render popover in `AudioPlayerBar`

File: `delivery/webapp/src/components/audio/AudioPlayerBar.tsx`

Import and place the popover immediately after the title/artist block in the track-info column (no layout changes beyond the icon itself):

```tsx
import { LocateSongsetsPopover } from "./LocateSongsetsPopover";

// inside the track info area:
<div className="flex items-center gap-3 min-w-0 flex-1 lg:flex-none">
  <div className="shrink-0 w-10 h-10 lg:w-12 lg:h-12 rounded-md bg-muted flex items-center justify-center">
    <Music className="size-5 lg:size-6 text-muted-foreground" />
  </div>
  <div className="min-w-0 flex-1">
    <p className="font-medium text-sm truncate" data-testid="track-title">
      {currentTrack.title}
    </p>
    <p className="text-xs text-muted-foreground truncate" data-testid="track-artist">
      {currentTrack.artist}
    </p>
  </div>
  <LocateSongsetsPopover />
</div>
```

---

### Phase 4: Songset detail page — highlight and scroll

#### 4a. Add stable DOM marker to song rows

File: `delivery/webapp/src/components/songset/SongList.tsx`

Add a stable `data-song-id` attribute to each `SortableSongItem` root:

```tsx
<div
  ref={setNodeRef}
  style={style}
  data-song-id={item.songId}
  className={cn(
    "group",
    isDragging && "opacity-50"
  )}
>
```

#### 4b. Consume `?highlightSong=` and pass it down

File: `delivery/webapp/src/app/songsets/[id]/SongsetEditorClient.tsx`

```typescript
const highlightSongId = searchParams.get("highlightSong");
```

Pass it through `SongsetEditor` to `SongList`. Clean only the highlight param, preserving others:

```typescript
useEffect(() => {
  if (!highlightSongId) return;
  const next = new URLSearchParams(searchParams.toString());
  next.delete("highlightSong");
  const query = next.toString();
  router.replace(`/songsets/${songsetId}${query ? `?${query}` : ""}`);
}, [highlightSongId, songsetId, searchParams, router]);
```

> The state used for scrolling must be retained in React state; removing the URL param should not clear `highlightSongId` until the scroll/highlight effect has run.

#### 4c. Thread `highlightSongId` through the editor chain

Files:  
- `delivery/webapp/src/components/songset/SongsetEditor.tsx`  
- `delivery/webapp/src/components/songset/SongList.tsx`

Add `highlightSongId?: string | null` to both prop interfaces and pass it through unchanged.

#### 4d. Robust scroll-to and highlight effect

File: `delivery/webapp/src/components/songset/SongList.tsx`

Use an effect with `requestAnimationFrame` and a short retry loop instead of a fixed timeout. This handles drag-and-drop layout shifts and hydration more reliably.

```typescript
const [highlightedSongId, setHighlightedSongId] = useState<string | null>(null);

useEffect(() => {
  if (!highlightSongId) return;

  let raf: number;
  let attempts = 0;
  const maxAttempts = 20; // ~1 second at 50 ms per frame, adjusted by rAF

  const tryScroll = () => {
    const el = document.querySelector(
      `[data-song-id="${CSS.escape(highlightSongId)}"]`
    );
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      setHighlightedSongId(highlightSongId);
      return;
    }
    if (++attempts < maxAttempts) {
      setTimeout(() => {
        raf = requestAnimationFrame(tryScroll);
      }, 50);
    }
  };

  raf = requestAnimationFrame(tryScroll);

  const dismissTimer = setTimeout(() => setHighlightedSongId(null), 3000);

  return () => {
    cancelAnimationFrame(raf);
    clearTimeout(dismissTimer);
  };
}, [highlightSongId]);
```

Pass `isHighlighted={highlightedSongId === item.songId}` to `SortableSongItem`.

#### 4e. Highlight styling

File: `delivery/webapp/src/components/songset/SongList.tsx`

Add `isHighlighted?: boolean` to `SortableSongItemProps` and apply a non-intrusive ring animation to the `<Card>`:

```tsx
<Card className={cn(
  "border-border/50 hover:border-border transition-colors",
  isPlaying && "border-primary/30 bg-primary/5",
  confirmRemove && "border-destructive/40 bg-destructive/5",
  isHighlighted && "ring-2 ring-primary border-primary animate-pulse"
)}>
```

---

## Implementation Order

1. **Phase 1**: Enrich `AudioTrack`, `PlaySongOptions`, and all `playSong/play` call sites.
2. **Phase 2**: Add DB index, implement `findSongsetsContainingSong`, create API route.
3. **Phase 3**: Build and integrate `LocateSongsetsPopover` into `AudioPlayerBar`.
4. **Phase 4**: Add `data-song-id`, thread `highlightSongId`, implement scroll/highlight, clean URL carefully.

## Files to Create

| File | Description |
|------|-------------|
| `delivery/webapp/src/components/audio/LocateSongsetsPopover.tsx` | Player bar popover for reverse songset lookup |
| `delivery/webapp/src/app/api/songs/[id]/songsets/route.ts` | API endpoint returning containing songsets |

## Files to Modify

| File | Changes |
|------|---------|
| `delivery/webapp/src/contexts/AudioPlayerContext.tsx` | Add `songId`, `originSongsetId` to `AudioTrack` |
| `delivery/webapp/src/hooks/useAudioPlayer.ts` | Add `originSongsetId` to `PlaySongOptions`; pass fields into `playSong` |
| `delivery/webapp/src/components/songset/SongList.tsx` | Pass origin/songId in `play()`; add `data-song-id`; scroll+highlight effect; `isHighlighted` prop |
| `delivery/webapp/src/components/songset/SongsetEditor.tsx` | Thread `highlightSongId` and `songsetId` to `SongList` |
| `delivery/webapp/src/app/songsets/[id]/SongsetEditorClient.tsx` | Read `?highlightSong=`; pass to editor; clean URL safely |
| `delivery/webapp/src/components/audio/AudioPlayerBar.tsx` | Render `LocateSongsetsPopover` next to track metadata |
| `delivery/webapp/src/lib/db/songsets.ts` | Add `findSongsetsContainingSong` |
| `delivery/webapp/src/db/schema.ts` | Add `idx_songset_items_song_id` index |
| Other song play call sites | Add `songId` (no `originSongsetId`) when calling `playSong` / `play` |

## Testing

### Unit / component tests

1. **DB helper**: mock `db`/subquery output; verify `findSongsetsContainingSong` returns songsets sorted with origin first, computes `itemCount` correctly, handles empty results, and includes `owner`.
2. **API route**: auth rejection, 400 on empty `id`, passes `origin` query to helper, returns 500 on unexpected error.
3. **`LocateSongsetsPopover`**: hidden for non-song tracks; hidden when `songId` is absent; fetches on open; displays loading/error/empty states; marks origin; navigates with `?highlightSong=` on click.
4. **`SongList` highlight effect**: with `highlightSongId`, waits for DOM element with matching `data-song-id`, scrolls, applies highlight class, clears after delay; cleans up timers on unmount.

### Integration / E2E scenarios

5. Play a song from Songset A → locate button visible → popover lists Songsets A, B, C with A marked Origin → click A → navigates to `/songsets/A?highlightSong=...` → target song centered and highlighted.
6. Play a song from Browse Sheet (no origin) → locate lists all containing songsets, none marked Origin.
7. Play a transition or lyrics-loop track → locate button hidden.
8. Song not in any songset → popover shows "not in any of your songsets".
9. After navigation to highlighted song, the `highlightSong` param is removed while `?new=true` or `?share=true` remain.

### Manual checks

10. Confirm no horizontal overflow on narrow mobile viewports (popover width `w-72`).
11. Confirm keyboard focus returns to the locate trigger after the popover closes.
12. Confirm the new DB index is generated and applied.

## Edge Cases & Considerations

- **Deleted song / removed from all sets**: API returns empty list; UI shows "not in any of your songsets".
- **Same song, different recordings**: Lookup is by `songId`, consistent with original plan. All recordings of the same song match.
- **No sharing yet**: `owner` is always the current user. The API shape is future-proofed to include collaborators/owners later.
- **Slow DOM / drag-and-drop**: Scroll effect retries briefly with `requestAnimationFrame` and a timeout instead of a single fixed delay.
- **URL round-trip safety**: `encodeURIComponent` and `CSS.escape` are used when interpolating IDs into URLs and selectors.
- **Auth/authorization**: Endpoint requires session; DB enforces `userId` isolation.
- **Operational logging**: Use the existing `timePageLoad` wrapper for query timing; log route errors via `console.error`.

## Migration Notes

Before deploying, run the Drizzle migration generated by the new index. The `songId` reverse lookup queries will not be used until the popover code is live, but the index should exist before the feature is enabled.
