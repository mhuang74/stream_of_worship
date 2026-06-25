# Songset Render Failed Error Tooltip

## Problem

When a SongSet's render fails, the "Render failed" status badge shows no error details. Users have no way to see *why* the render failed. The `render_jobs` table stores an `errorMessage` column (schema.ts:233), but this data is never surfaced.

## Solution

Display the render job's `errorMessage` as a tooltip when hovering over the "Render failed" badge on **all screens** that render `RenderStatusBadge`. When `errorMessage` is null/empty, show a fallback message with the failed render job's datetime stamp.

### Key Decisions

1. **Tooltip implementation**: Use the existing base-ui `Tooltip` component (`webapp/src/components/ui/tooltip.tsx`).
2. **Null errorMessage fallback**: Show `"Render failed at <datetime>"` using the render job's `updatedAt` timestamp, since the render page doesn't display error details either.

## Screens Using RenderStatusBadge

Two render sites found:

1. **Songsets list page** — `SongsetRow.tsx:220` (data via `listSongsetSummaries`)
2. **Songset editor page** — `SongsetEditor.tsx:258` (data via `getSongsetEditorData`)

Both DB functions already `LEFT JOIN renderJobs ON renderJobs.id = songsets.latestRenderJobId`. When `renderState === "failed"`, the latest render job IS the failed job (per `mapRenderStateFromSnapshot` at lib/db/songsets.ts:222-227). So selecting `renderJobs.errorMessage` and `renderJobs.updatedAt` from the existing join gives us the failed job's error details — no additional join needed.

## Data Flows

**Flow 1 — List page:**
```
listSongsetSummaries (lib/db/songsets.ts)
  → /api/songsets GET (route.ts) — passthrough
    → SongsetsClient (transformSongsets)
      → SongsetList → SongsetRow → RenderStatusBadge
```

**Flow 2 — Editor page:**
```
getSongsetEditorData (lib/db/songsets.ts)
  → /songsets/[id]/page.tsx (server component, serializes)
    → SongsetEditorClient (ApiSongset/ApiResponse interfaces)
      → SongsetEditor → RenderStatusBadge
```

## Files to Modify

### 1. `webapp/src/lib/db/songsets.ts`

#### 1a. Add fields to `SongsetListItem` interface (lines 104-116)

```typescript
export interface SongsetListItem {
  id: string;
  name: string;
  description: string | null;
  createdAt: Date;
  updatedAt: Date;
  renderState: RenderState;
  itemCount: number;
  durationSeconds: number | null;
  latestRenderJobId: string | null;
  lastFailedRenderJobId: string | null;
  lastCompletedRenderJobId: string | null;
  renderErrorMessage: string | null;   // NEW
  failedAt: Date | null;               // NEW
}
```

Field naming rationale:
- `renderErrorMessage` (not `errorMessage`) to clarify it's the render job's error.
- `failedAt` — the `updatedAt` of the render job when marked failed. `failRenderJob` (job-manager.ts:320-328) and `recoverOrphanedJobs` (job-manager.ts:411-419) both set `updatedAt: now` when transitioning to failed. `completedAt` is NOT set for failed jobs, so `updatedAt` is the correct failure timestamp.

Since `SongsetDetail extends SongsetListItem` (line 148), `SongsetDetail` inherits these fields automatically.

#### 1b. Update `listSongsetSummaries` (lines 275-346)

Add to `.select()` (after `latestJobCompletedAt`):
```typescript
renderErrorMessage: renderJobs.errorMessage,
latestJobUpdatedAt: renderJobs.updatedAt,
```

Add to `.groupBy()` (after `renderJobs.completedAt`):
```typescript
renderJobs.errorMessage,
renderJobs.updatedAt
```

Add to the return mapping `rows.map(...)`:
```typescript
renderErrorMessage: row.renderErrorMessage,
failedAt: row.latestJobUpdatedAt,
```

#### 1c. Update `getSongsetEditorData` (lines 348-495)

Add to `.select()` (after `latestJobCompletedAt`, line 364):
```typescript
renderErrorMessage: renderJobs.errorMessage,
latestJobUpdatedAt: renderJobs.updatedAt,
```

Add to the return object (after `lastCompletedRenderJobId`, line 481):
```typescript
renderErrorMessage: row.renderErrorMessage,
failedAt: row.latestJobUpdatedAt,
```

#### 1d. Update `createSongset` return (lines 744-768)

Add to the returned object:
```typescript
renderErrorMessage: null,
failedAt: null,
```

#### 1e. Update `updateSongset` return (lines 770-820)

Add to the returned object (after `lastCompletedRenderJobId`):
```typescript
renderErrorMessage: null,
failedAt: null,
```

Note: `updateSongset` uses the deprecated `computeRenderState` and doesn't join `renderJobs`. Since this function is for renaming/description updates (not render operations), null is the correct value. The editor page reloads via `getSongsetEditorData` after such updates, which will fetch the real values.

#### 1f. Update deprecated `getSongset` return (lines 662-742)

Add to the returned object (after `lastCompletedRenderJobId`):
```typescript
renderErrorMessage: null,
failedAt: null,
```

Note: `getSongset` is deprecated but still used by `duplicateSongset`. The duplicated songset is always unrendered, so null is correct.

### 2. `webapp/src/app/songsets/page.tsx` (list page server component)

Serialize `failedAt` to ISO string in the `.map()` (lines 20-24):

```typescript
songsets: result.songsets.map((songset) => ({
  ...songset,
  createdAt: songset.createdAt.toISOString(),
  updatedAt: songset.updatedAt.toISOString(),
  failedAt: songset.failedAt?.toISOString() ?? null,  // NEW
})),
```

`renderErrorMessage` is already `string | null`, handled by the spread.

### 3. `webapp/src/app/songsets/SongsetsClient.tsx` (list page client)

#### 3a. Add fields to `ApiSongset` interface (lines 16-28)

```typescript
renderErrorMessage: string | null;   // NEW
failedAt: string | null;             // NEW
```

#### 3b. Map fields in `transformSongsets` (lines 35-49)

```typescript
renderErrorMessage: songset.renderErrorMessage,
failedAt: songset.failedAt ? new Date(songset.failedAt) : null,
```

### 4. `webapp/src/components/songset/SongsetList.tsx`

#### 4a. Add fields to `Songset` interface (lines 21-33)

```typescript
renderErrorMessage?: string | null;   // NEW
failedAt?: Date | null;               // NEW
```

No other change needed — `SongsetRow` is rendered with `{...songset}` spread (line 245), so new fields pass through automatically.

### 5. `webapp/src/components/songset/SongsetRow.tsx`

#### 5a. Add fields to `SongsetRowProps` interface (lines 35-57)

```typescript
renderErrorMessage?: string | null;   // NEW
failedAt?: Date | null;               // NEW
```

#### 5b. Destructure new props (lines 59-79)

Add `renderErrorMessage` and `failedAt` to the destructured params.

#### 5c. Pass props to `RenderStatusBadge` (line 220)

```tsx
<RenderStatusBadge
  state={renderState}
  errorMessage={renderErrorMessage}
  failedAt={failedAt}
/>
```

### 6. `webapp/src/app/songsets/[id]/page.tsx` (editor page server component)

Serialize `failedAt` to ISO string in `initialData` mapping (lines 28-40):

```typescript
renderState: songset.renderState,
renderErrorMessage: songset.renderErrorMessage,                    // NEW
failedAt: songset.failedAt?.toISOString() ?? null,                 // NEW
itemCount: songset.itemCount,
```

### 7. `webapp/src/app/songsets/[id]/SongsetEditorClient.tsx` (editor page client)

#### 7a. Add fields to `ApiSongset` interface (lines 24-37)

```typescript
renderErrorMessage: string | null;   // NEW
failedAt: string | null;             // NEW
```

#### 7b. Add fields to `ApiResponse` interface (lines 66-80)

```typescript
renderErrorMessage: string | null;   // NEW
failedAt: string | null;             // NEW
```

#### 7c. Add to `songset` state initialization (lines 113-126)

```typescript
renderErrorMessage: initialData.renderErrorMessage,
failedAt: initialData.failedAt ? new Date(initialData.failedAt) : null,
```

### 8. `webapp/src/components/songset/SongsetEditor.tsx`

#### 8a. Add fields to `SongsetEditorProps.songset` (lines 49-60)

```typescript
renderErrorMessage?: string | null;   // NEW
failedAt?: Date | null;               // NEW
```

Note: `failedAt` comes as ISO string from the client state. It is converted to `Date` in `SongsetEditorClient` when initializing state (use `new Date(...)`), matching the `SongsetsClient` pattern. Then `SongsetEditor` receives a `Date | null`.

#### 8b. Pass props to `RenderStatusBadge` (line 258)

```tsx
<RenderStatusBadge
  state={songset.renderState}
  errorMessage={songset.renderErrorMessage}
  failedAt={songset.failedAt}
/>
```

### 9. `webapp/src/components/songset/RenderStatusBadge.tsx` (core change)

#### 9a. Add imports

```typescript
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
```

#### 9b. Extend props interface (lines 9-12)

```typescript
interface RenderStatusBadgeProps {
  state: RenderState
  errorMessage?: string | null
  failedAt?: Date | null
  className?: string
}
```

#### 9c. Add helper function

```typescript
function formatFailedAt(date: Date): string {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date)
}
```

#### 9d. Modify component

```typescript
export function RenderStatusBadge({
  state,
  errorMessage,
  failedAt,
  className,
}: RenderStatusBadgeProps) {
  const config = STATE_CONFIG[state] || STATE_CONFIG.unrendered
  const Icon = config.icon

  const badge = (
    <Badge variant={config.variant} className={cn("gap-1", className)}>
      <Icon className={cn("size-3", config.iconClass)} />
      {config.label}
    </Badge>
  )

  if (state === "failed") {
    const tooltipText = errorMessage?.trim()
      ? errorMessage
      : failedAt
        ? `Render failed at ${formatFailedAt(failedAt)}`
        : null

    if (tooltipText) {
      return (
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              {badge}
            </TooltipTrigger>
            <TooltipContent className="max-w-80 whitespace-pre-wrap break-words">
              <p>{tooltipText}</p>
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      )
    }
  }

  return badge
}
```

Design notes:
- `TooltipProvider` wraps locally (no global provider in layout.tsx) — same pattern as `OfflineStatus.tsx:139` and `RenderForm.tsx:173`.
- `TooltipTrigger asChild` renders the `Badge` as the trigger (no extra wrapper DOM).
- `max-w-80` + `whitespace-pre-wrap break-words` handles long error messages.
- When both `errorMessage` and `failedAt` are null (edge case), no tooltip — badge behaves as before.
- Tooltip only appears for `state === "failed"`.

## Files to Modify (Tests)

### 10. `webapp/src/test/components/songset/RenderStatusBadge.test.tsx`

Add test cases:

```typescript
describe("failed state with error message", () => {
  it("renders tooltip with error message when failed and errorMessage provided", () => {
    render(<RenderStatusBadge state="failed" errorMessage="FFmpeg encoding failed: exit code 1" />);
    expect(screen.getByText("Render failed")).toBeInTheDocument();
    expect(screen.getByText("FFmpeg encoding failed: exit code 1")).toBeInTheDocument();
  });

  it("renders tooltip with fallback datetime when errorMessage is null", () => {
    render(<RenderStatusBadge state="failed" errorMessage={null} failedAt={new Date("2026-06-18T15:45:00Z")} />);
    expect(screen.getByText(/Render failed at/)).toBeInTheDocument();
  });

  it("renders tooltip with fallback datetime when errorMessage is empty string", () => {
    render(<RenderStatusBadge state="failed" errorMessage="" failedAt={new Date("2026-06-18T15:45:00Z")} />);
    expect(screen.getByText(/Render failed at/)).toBeInTheDocument();
  });

  it("renders tooltip with fallback datetime when errorMessage is whitespace only", () => {
    render(<RenderStatusBadge state="failed" errorMessage="   " failedAt={new Date("2026-06-18T15:45:00Z")} />);
    expect(screen.getByText(/Render failed at/)).toBeInTheDocument();
  });

  it("renders no tooltip when failed but both errorMessage and failedAt are null", () => {
    render(<RenderStatusBadge state="failed" />);
    expect(screen.getByText("Render failed")).toBeInTheDocument();
    expect(screen.queryByText(/Render failed at/)).not.toBeInTheDocument();
  });
});

describe("non-failed states with error message", () => {
  it("does not render tooltip when state is fresh even if errorMessage provided", () => {
    render(<RenderStatusBadge state="fresh" errorMessage="some error" />);
    expect(screen.getByText("Rendered")).toBeInTheDocument();
    expect(screen.queryByText("some error")).not.toBeInTheDocument();
  });

  it("does not render tooltip when state is rendering", () => {
    render(<RenderStatusBadge state="rendering" errorMessage="some error" />);
    expect(screen.queryByText("some error")).not.toBeInTheDocument();
  });
});
```

### 11. `webapp/src/test/components/songset/SongsetRow.test.tsx`

Add to existing mock `defaultProps`:
```typescript
renderErrorMessage: null,
failedAt: null,
```

Add test cases:
```typescript
describe("render failed error tooltip", () => {
  it("passes errorMessage to RenderStatusBadge when renderState is failed", () => {
    renderRow({
      renderState: "failed" as RenderState,
      renderErrorMessage: "FFmpeg encoding failed",
      failedAt: new Date("2026-06-18T15:45:00Z"),
    });
    expect(screen.getByText("Render failed")).toBeInTheDocument();
    expect(screen.getByText("FFmpeg encoding failed")).toBeInTheDocument();
  });

  it("shows fallback datetime when renderState is failed and errorMessage is null", () => {
    renderRow({
      renderState: "failed" as RenderState,
      renderErrorMessage: null,
      failedAt: new Date("2026-06-18T15:45:00Z"),
    });
    expect(screen.getByText(/Render failed at/)).toBeInTheDocument();
  });
});
```

### 12. `webapp/src/test/components/songset/SongsetEditor.test.tsx`

Add to existing `mockSongset`:
```typescript
renderErrorMessage: null,
failedAt: null,
```

Add test cases:
```typescript
describe("render failed error tooltip", () => {
  it("shows error message tooltip when renderState is failed and errorMessage provided", () => {
    renderEditor({
      songset: {
        ...mockSongset,
        renderState: "failed" as RenderState,
        renderErrorMessage: "FFmpeg encoding failed",
        failedAt: new Date("2026-06-18T15:45:00Z"),
      },
    });
    expect(screen.getByText("Render failed")).toBeInTheDocument();
    expect(screen.getByText("FFmpeg encoding failed")).toBeInTheDocument();
  });

  it("shows fallback datetime tooltip when renderState is failed and errorMessage is null", () => {
    renderEditor({
      songset: {
        ...mockSongset,
        renderState: "failed" as RenderState,
        renderErrorMessage: null,
        failedAt: new Date("2026-06-18T15:45:00Z"),
      },
    });
    expect(screen.getByText(/Render failed at/)).toBeInTheDocument();
  });
});
```

### 13. `webapp/src/test/api/songsets/db.test.ts`

Update existing `listSongsetSummaries` mock rows to include new fields:
```typescript
renderErrorMessage: null,
latestJobUpdatedAt: null,
```

Add new test:
```typescript
it("returns renderErrorMessage and failedAt from latest render job", async () => {
  const mockRows = [{
    id: "ss-1", name: "Failed Songset", description: null,
    createdAt: new Date("2024-01-01"), updatedAt: new Date("2024-01-02"),
    latestRenderJobId: "job-1", lastFailedRenderJobId: "job-1", lastCompletedRenderJobId: null,
    itemCount: 2, durationSeconds: 90, latestItemUpdatedAt: null,
    latestJobStatus: "failed", latestJobCompletedAt: null,
    renderErrorMessage: "FFmpeg encoding failed: exit code 1",
    latestJobUpdatedAt: new Date("2024-01-02T12:00:00Z"),
  }];
  const chain = createSelectChain(mockRows);
  const countChain = createSelectChain([{ count: 1 }]);
  vi.mocked(db.select).mockReturnValueOnce(chain as any).mockReturnValueOnce(countChain as any);

  const result = await listSongsetSummaries(1, 50, 0);

  expect(result.songsets[0].renderState).toBe("failed");
  expect(result.songsets[0].renderErrorMessage).toBe("FFmpeg encoding failed: exit code 1");
  expect(result.songsets[0].failedAt).toEqual(new Date("2024-01-02T12:00:00Z"));
});
```

Also update `getSongsetEditorData` test mock rows similarly (add `renderErrorMessage` and `latestJobUpdatedAt` to existing mocks).

## Files NOT Modified

- **`webapp/src/app/api/songsets/route.ts`** — GET returns `listSongsetSummaries` result directly as JSON. New fields auto-included.
- **`webapp/src/app/api/songsets/[id]/route.ts`** — PATCH returns `updateSongset` result. New null fields auto-included.
- **`webapp/src/app/api/songsets/[id]/duplicate/route.ts`** — Returns `duplicateSongset` result. New null fields auto-included.
- **`webapp/src/db/schema.ts`** — `errorMessage` and `updatedAt` columns already exist on `renderJobs`.
- **`webapp/src/app/layout.tsx`** — No global `TooltipProvider`. Each tooltip wraps locally.

## Screens NOT Needing Changes (no RenderStatusBadge)

- Play page (`play/page.tsx`, `play/controller/page.tsx`) — uses `renderState` but no `RenderStatusBadge`
- Share page (`share/[token]/page.tsx`) — uses `renderState` but no `RenderStatusBadge`
- PrePlayCard (`components/play/PrePlayCard.tsx`) — own UI, no `RenderStatusBadge`
- Render page (`RenderPageClient.tsx`) — no `RenderStatusBadge`

## Verification

```bash
cd webapp && pnpm lint
cd webapp && pnpm build
cd webapp && pnpm test -- --run src/test/components/songset/RenderStatusBadge.test.tsx
cd webapp && pnpm test -- --run src/test/components/songset/SongsetRow.test.tsx
cd webapp && pnpm test -- --run src/test/components/songset/SongsetEditor.test.tsx
cd webapp && pnpm test -- --run src/test/api/songsets/db.test.ts
```
