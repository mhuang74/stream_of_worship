# Songset Render Failed Error Tooltip

## Problem

When a SongSet's render fails, the Songsets list page (`/songsets`) shows a "Render failed" status badge, but provides no additional error details. Users have no way to see *why* the render failed without investigating externally. The `render_jobs` table already stores an `errorMessage` column (schema.ts:233), but this data is never surfaced to the user.

## Solution

Display the render job's `errorMessage` as a tooltip when hovering over the "Render failed" badge. When `errorMessage` is null/empty, show a fallback message containing the datetime stamp of when the render failed (the render job's `updatedAt`).

### Key Decisions (from user clarification)

1. **Tooltip implementation**: Use the existing base-ui `Tooltip` component (`webapp/src/components/ui/tooltip.tsx`), consistent with other tooltips in the app (e.g., `OfflineStatus.tsx`, `RenderForm.tsx`).
2. **Null errorMessage fallback**: Show a generic message with the failed render job's datetime stamp (e.g., `"Render failed at Jun 18, 2026, 3:45 PM"`), since the render page does not display error details either.

## Data Flow

The data flows through 5 layers, all of which need modification:

```
DB query (listSongsetSummaries)
  → API route (/api/songsets GET) — no change, passes through
    → SongsetsClient (transformSongsets)
      → SongsetList
        → SongsetRow
          → RenderStatusBadge (tooltip display)
```

The `listSongsetSummaries` function already performs a `LEFT JOIN` on `renderJobs` (joined on `renderJobs.id = songsets.latestRenderJobId`). When the render state is `"failed"`, the latest render job IS the failed job (per `mapRenderStateFromSnapshot` at lib/db/songsets.ts:222-227). Therefore, selecting `renderJobs.errorMessage` and `renderJobs.updatedAt` from the existing join gives us the failed job's error details — no additional join needed.

## Files to Modify

### 1. `webapp/src/lib/db/songsets.ts`

#### Change 1a: Add fields to `SongsetListItem` interface (line 104-116)

Add two new fields to the interface:

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
- `renderErrorMessage` (not `errorMessage`) to avoid confusion with other error fields and clarify it's the render job's error.
- `failedAt` — the `updatedAt` timestamp of the render job when it was marked as failed. The `failRenderJob` function (job-manager.ts:320-328) and `recoverOrphanedJobs` (job-manager.ts:411-419) both set `updatedAt: now` when transitioning a job to failed status. The `completedAt` field is NOT set for failed jobs (only `markJobCompleted` sets it), so `updatedAt` is the correct timestamp for failure time.

#### Change 1b: Add fields to `listSongsetSummaries` select query (line 282-296)

In the `.select({...})` call, add two new fields from the existing `renderJobs` join:

```typescript
latestJobStatus: renderJobs.status,
latestJobCompletedAt: renderJobs.completedAt,
renderErrorMessage: renderJobs.errorMessage,    // NEW
latestJobUpdatedAt: renderJobs.updatedAt,       // NEW (alias to failedAt in mapping)
```

#### Change 1c: Add fields to the return mapping (line 325-343)

In the `rows.map((row) => ({ ... }))` block, add:

```typescript
renderErrorMessage: row.renderErrorMessage,
failedAt: row.latestJobUpdatedAt,
```

Also add `renderJobs.errorMessage` and `renderJobs.updatedAt` to the `.groupBy()` call (lines 302-313) since they come from the joined table:

```typescript
.groupBy(
  songsets.id,
  songsets.name,
  songsets.description,
  songsets.createdAt,
  songsets.updatedAt,
  songsets.latestRenderJobId,
  songsets.lastFailedRenderJobId,
  songsets.lastCompletedRenderJobId,
  renderJobs.status,
  renderJobs.completedAt,
  renderJobs.errorMessage,       // NEW
  renderJobs.updatedAt           // NEW
)
```

### 2. `webapp/src/app/songsets/page.tsx`

#### Change 2a: Serialize `failedAt` to ISO string (lines 20-24)

The `listSongsetSummaries` returns `failedAt` as a `Date | null`. The server component must serialize it to an ISO string before passing to the client component. Update the `.map()` to convert `failedAt`:

```typescript
songsets: result.songsets.map((songset) => ({
  ...songset,
  createdAt: songset.createdAt.toISOString(),
  updatedAt: songset.updatedAt.toISOString(),
  failedAt: songset.failedAt?.toISOString() ?? null,  // NEW
})),
```

The `renderErrorMessage` field is already a `string | null`, so the spread (`...songset`) handles it automatically.

### 3. `webapp/src/app/songsets/SongsetsClient.tsx`

#### Change 3a: Add fields to `ApiSongset` interface (lines 16-28)

```typescript
interface ApiSongset {
  id: string;
  name: string;
  description: string | null;
  createdAt: string;
  updatedAt: string;
  renderState: RenderState;
  itemCount: number;
  durationSeconds: number | null;
  latestRenderJobId: string | null;
  lastFailedRenderJobId: string | null;
  lastCompletedRenderJobId: string | null;
  renderErrorMessage: string | null;   // NEW
  failedAt: string | null;             // NEW (ISO string)
}
```

#### Change 3b: Map fields in `transformSongsets` (lines 35-49)

```typescript
function transformSongsets(songsets: ApiSongset[]): Songset[] {
  return songsets.map((songset) => ({
    id: songset.id,
    name: songset.name,
    description: songset.description,
    itemCount: songset.itemCount,
    durationSeconds: songset.durationSeconds ?? undefined,
    updatedAt: new Date(songset.updatedAt),
    renderState: songset.renderState,
    latestRenderJobId: songset.latestRenderJobId,
    lastCompletedRenderJobId: songset.lastCompletedRenderJobId,
    renderErrorMessage: songset.renderErrorMessage,                    // NEW
    failedAt: songset.failedAt ? new Date(songset.failedAt) : null,   // NEW
    isOfflineAvailable: false,
    isArtifactsStale: songset.renderState === "stale",
  }));
}
```

### 4. `webapp/src/components/songset/SongsetList.tsx`

#### Change 4a: Add fields to `Songset` interface (lines 21-33)

```typescript
export interface Songset {
  id: string;
  name: string;
  description?: string | null;
  itemCount: number;
  durationSeconds?: number;
  updatedAt: Date;
  renderState: RenderState;
  isOfflineAvailable?: boolean;
  isArtifactsStale?: boolean;
  latestRenderJobId: string | null;
  lastCompletedRenderJobId: string | null;
  renderErrorMessage?: string | null;   // NEW
  failedAt?: Date | null;               // NEW
}
```

#### Change 4b: Pass props to `SongsetRow` (lines 242-256)

The `SongsetRow` is rendered with `{...songset}` spread (line 245), so the new fields are automatically passed through. No explicit change needed here — the spread already forwards `renderErrorMessage` and `failedAt`.

**Verification**: Line 243-255 uses `<SongsetRow key={songset.id} {...songset} onRender={...} ... />`. The spread forwards all `Songset` fields as props. Confirmed no change needed.

### 5. `webapp/src/components/songset/SongsetRow.tsx`

#### Change 5a: Add fields to `SongsetRowProps` interface (lines 35-57)

```typescript
export interface SongsetRowProps {
  id: string;
  name: string;
  description?: string | null;
  itemCount: number;
  durationSeconds?: number;
  updatedAt: Date;
  renderState: RenderState;
  isOfflineAvailable?: boolean;
  isArtifactsStale?: boolean;
  latestRenderJobId: string | null;
  lastCompletedRenderJobId: string | null;
  renderErrorMessage?: string | null;   // NEW
  failedAt?: Date | null;               // NEW
  onRender?: () => void;
  // ... rest unchanged
}
```

#### Change 5b: Destructure new props (lines 59-79)

Add `renderErrorMessage` and `failedAt` to the destructured props:

```typescript
export function SongsetRow({
  id,
  name,
  description,
  itemCount,
  durationSeconds,
  updatedAt,
  renderState,
  isOfflineAvailable = false,
  isArtifactsStale = false,
  lastCompletedRenderJobId,
  renderErrorMessage,    // NEW
  failedAt,               // NEW
  onRender,
  // ... rest unchanged
}: SongsetRowProps) {
```

#### Change 5c: Pass props to `RenderStatusBadge` (line 220)

Change from:

```tsx
<RenderStatusBadge state={renderState} />
```

To:

```tsx
<RenderStatusBadge
  state={renderState}
  errorMessage={renderErrorMessage}
  failedAt={failedAt}
/>
```

### 6. `webapp/src/components/songset/RenderStatusBadge.tsx`

This is the core change — adding tooltip display for the failed state.

#### Change 6a: Add imports

```typescript
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
```

#### Change 6b: Extend props interface (lines 9-12)

```typescript
interface RenderStatusBadgeProps {
  state: RenderState
  errorMessage?: string | null
  failedAt?: Date | null
  className?: string
}
```

#### Change 6c: Add helper function for fallback message

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

#### Change 6d: Modify component to render tooltip when failed

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

  // Only show tooltip for failed state with some info to display
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
- `TooltipTrigger asChild` renders the `Badge` as the trigger element (no extra wrapper DOM).
- `max-w-80` allows wider tooltip for long error messages; `whitespace-pre-wrap break-words` preserves newlines and wraps long lines.
- When both `errorMessage` and `failedAt` are null (edge case), no tooltip is rendered — badge behaves as before.
- The tooltip only appears for `state === "failed"`. Other states render the plain badge with no tooltip.

## Files to Modify (Tests)

### 7. `webapp/src/test/components/songset/RenderStatusBadge.test.tsx`

Add test cases for the tooltip behavior:

```typescript
describe("failed state with error message", () => {
  it("renders tooltip with error message when failed and errorMessage provided", () => {
    render(
      <RenderStatusBadge
        state="failed"
        errorMessage="FFmpeg encoding failed: exit code 1"
      />
    );
    expect(screen.getByText("Render failed")).toBeInTheDocument();
    expect(screen.getByText("FFmpeg encoding failed: exit code 1")).toBeInTheDocument();
  });

  it("renders tooltip with fallback datetime when errorMessage is null", () => {
    const failedAt = new Date("2026-06-18T15:45:00Z");
    render(
      <RenderStatusBadge state="failed" errorMessage={null} failedAt={failedAt} />
    );
    expect(screen.getByText("Render failed")).toBeInTheDocument();
    expect(screen.getByText(/Render failed at/)).toBeInTheDocument();
  });

  it("renders tooltip with fallback datetime when errorMessage is empty string", () => {
    const failedAt = new Date("2026-06-18T15:45:00Z");
    render(
      <RenderStatusBadge state="failed" errorMessage="" failedAt={failedAt} />
    );
    expect(screen.getByText(/Render failed at/)).toBeInTheDocument();
  });

  it("renders tooltip with fallback datetime when errorMessage is whitespace only", () => {
    const failedAt = new Date("2026-06-18T15:45:00Z");
    render(
      <RenderStatusBadge state="failed" errorMessage="   " failedAt={failedAt} />
    );
    expect(screen.getByText(/Render failed at/)).toBeInTheDocument();
  });

  it("renders no tooltip when failed but both errorMessage and failedAt are null", () => {
    render(<RenderStatusBadge state="failed" />);
    expect(screen.getByText("Render failed")).toBeInTheDocument();
    // No tooltip content should be present
    expect(screen.queryByText(/Render failed at/)).not.toBeInTheDocument();
  });
});

describe("non-failed states with error message", () => {
  it("does not render tooltip when state is fresh even if errorMessage provided", () => {
    render(
      <RenderStatusBadge state="fresh" errorMessage="some error" />
    );
    expect(screen.getByText("Rendered")).toBeInTheDocument();
    expect(screen.queryByText("some error")).not.toBeInTheDocument();
  });

  it("does not render tooltip when state is rendering", () => {
    render(
      <RenderStatusBadge state="rendering" errorMessage="some error" />
    );
    expect(screen.getByText("Rendering")).toBeInTheDocument();
    expect(screen.queryByText("some error")).not.toBeInTheDocument();
  });
});
```

### 8. `webapp/src/test/components/songset/SongsetRow.test.tsx`

Add test cases verifying the error props are passed through to the badge:

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

### 9. `webapp/src/test/api/songsets/db.test.ts`

Update the `listSongsetSummaries` test mock rows to include the new fields, and add a test verifying the new fields are returned:

In existing test mock rows (lines 211-226, 270-285, 300-315), add:

```typescript
renderErrorMessage: null,
latestJobUpdatedAt: null,
```

Add a new test case:

```typescript
it("returns renderErrorMessage and failedAt from latest render job", async () => {
  const mockRows = [
    {
      id: "ss-1",
      name: "Failed Songset",
      description: null,
      createdAt: new Date("2024-01-01"),
      updatedAt: new Date("2024-01-02"),
      latestRenderJobId: "job-1",
      lastFailedRenderJobId: "job-1",
      lastCompletedRenderJobId: null,
      itemCount: 2,
      durationSeconds: 90,
      latestItemUpdatedAt: null,
      latestJobStatus: "failed",
      latestJobCompletedAt: null,
      renderErrorMessage: "FFmpeg encoding failed: exit code 1",
      latestJobUpdatedAt: new Date("2024-01-02T12:00:00Z"),
    },
  ];

  const chain = createSelectChain(mockRows);
  const countChain = createSelectChain([{ count: 1 }]);
  vi.mocked(db.select)
    .mockReturnValueOnce(chain as any)
    .mockReturnValueOnce(countChain as any);

  const result = await listSongsetSummaries(1, 50, 0);

  expect(result.songsets[0].renderState).toBe("failed");
  expect(result.songsets[0].renderErrorMessage).toBe("FFmpeg encoding failed: exit code 1");
  expect(result.songsets[0].failedAt).toEqual(new Date("2024-01-02T12:00:00Z"));
});
```

## Files NOT Modified

- **`webapp/src/app/api/songsets/route.ts`** — The GET handler returns `listSongsetSummaries` result directly as JSON. The new fields are automatically included. No change needed.
- **`webapp/src/db/schema.ts`** — The `errorMessage` and `updatedAt` columns already exist on `renderJobs` table. No schema change needed.
- **`webapp/src/app/layout.tsx`** — No global `TooltipProvider` added. Each tooltip wraps locally with its own `TooltipProvider`, matching the existing pattern.

## Verification

After implementation, run:

```bash
# Lint
cd webapp && pnpm lint

# Type check (via build)
cd webapp && pnpm build

# Tests
cd webapp && pnpm test -- --run src/test/components/songset/RenderStatusBadge.test.tsx
cd webapp && pnpm test -- --run src/test/components/songset/SongsetRow.test.tsx
cd webapp && pnpm test -- --run src/test/api/songsets/db.test.ts
```

## Visual Layout (After Changes)

```
┌─────────────────────────────────────────────────┐
│  Songset Name               [▶ Play] [⋮ KAB]   │
│  Description                                     │
│  ♪ 5 songs  ⏱ 12:30  Updated Jun 6             │
│  [⚠ Render failed]  ← hover shows tooltip       │
└─────────────────────────────────────────────────┘

Tooltip (on hover):
┌─────────────────────────────────────────────────┐
│ FFmpeg encoding failed: exit code 1             │
└─────────────────────────────────────────────────┘

OR (when errorMessage is null):
┌─────────────────────────────────────────────────┐
│ Render failed at Jun 18, 2026, 03:45 PM        │
└─────────────────────────────────────────────────┘
```
