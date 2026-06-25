# Songset Render Failed Error Tooltip & Inline Failure Alert (v3)

## Problem

When a SongSet's render fails, the "Render failed" status badge shows no error details. Users have no way to see *why* the render failed. The `render_jobs` table stores an `errorMessage` column (`schema.ts:233`), but this data is never surfaced.

## Goals

1. Surface the failed render job’s error message in the UI.
2. Sanitize and truncate the message so end users see a short, human-readable summary, not raw stack traces or infra internals.
3. Make the failure reason discoverable on both the list and editor pages:
   - **List page**: hover tooltip on the badge.
   - **Editor page**: visible inline failure alert plus the same hover tooltip.
4. Provide a fallback timestamp when no usable error message exists.

## Key Decisions

1. **Sanitization**: A new shared helper (`lib/render/error-message.ts`) trims whitespace, takes the first non-empty line, strips ANSI escape codes, truncates to 250 characters, and returns `null` for empty/whitespace-only messages.
2. **Truncation in list API**: `listSongsetSummaries` uses PostgreSQL `left(..., 250)` on the raw `errorMessage` so the `/api/songsets` payload never carries full worker tracebacks.
3. **Inline alert on editor**: `SongsetEditor.tsx` shows a `destructive` `Alert` below the app bar when `renderState === "failed"`, displaying the sanitized error or a fallback timestamp and a "Render again" button.
4. **Fallback timestamp source**: Use `renderJobs.startedAt ?? renderJobs.createdAt` so the time reflects approximately when the failing job ran, not the recovery-time `updatedAt` set by `recoverOrphanedJobs`.
5. **Tooltip tests**: Tests open the tooltip with `@testing-library/user-event` hover interactions because the app’s `TooltipContent` is rendered through a portal and is not present in the document until opened.

## Data Model

The `render_jobs` table already has the required columns (`errorMessage`, `startedAt`, `createdAt`, `updatedAt`). No schema migration is needed.

Add to `SongsetListItem` (and therefore `SongsetDetail`, which extends it):

```typescript
renderErrorMessage: string | null;   // sanitized/truncated summary
failedAt: Date | null;               // startedAt ?? createdAt of the failed job
```

## Files to Modify

### 1. New shared helper: `webapp/src/lib/render/error-message.ts`

```typescript
const RENDER_ERROR_SUMMARY_MAX_LENGTH = 250;

export function sanitizeRenderErrorMessage(
  message: string | null | undefined
): string | null {
  if (!message || typeof message !== "string") return null;
  let cleaned = message.replace(/\u001b\[[0-9;]*m/g, "");   // ANSI codes
  const firstLine = cleaned.split(/\r?\n/).find((line) => line.trim().length > 0);
  if (!firstLine) return null;
  const trimmed = firstLine.trim();
  if (trimmed.length === 0) return null;
  if (trimmed.length <= RENDER_ERROR_SUMMARY_MAX_LENGTH) return trimmed;
  return `${trimmed.slice(0, RENDER_ERROR_SUMMARY_MAX_LENGTH - 1)}…`;
}

export function formatRenderFailedAt(date: Date): string {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}
```

### 2. `webapp/src/lib/db/songsets.ts`

#### 2a. `SongsetListItem` interface

Add fields:

```typescript
renderErrorMessage: string | null;
failedAt: Date | null;
```

#### 2b. `listSongsetSummaries`

Select block:

```typescript
renderErrorMessage: sql<string | null>`left(nullif(trim(${renderJobs.errorMessage}), ''), 250)`,
latestJobStartedAt: renderJobs.startedAt,
latestJobCreatedAt: renderJobs.createdAt,
```

`GROUP BY` block (add):

```typescript
renderJobs.errorMessage,
renderJobs.startedAt,
renderJobs.createdAt,
```

Return mapping:

```typescript
renderErrorMessage: sanitizeRenderErrorMessage(row.renderErrorMessage),
failedAt: row.latestJobStartedAt ?? row.latestJobCreatedAt,
```

#### 2c. `getSongsetEditorData`

Select block:

```typescript
renderErrorMessage: renderJobs.errorMessage,
latestJobStartedAt: renderJobs.startedAt,
latestJobCreatedAt: renderJobs.createdAt,
```

Return object:

```typescript
renderErrorMessage: sanitizeRenderErrorMessage(row.renderErrorMessage),
failedAt: row.latestJobStartedAt ?? row.latestJobCreatedAt,
```

#### 2d. `createSongset`, `updateSongset`, `getSongset` returns

Add to each returned object:

```typescript
renderErrorMessage: null,
failedAt: null,
```

(These functions either create an unrendered songset or perform non-render mutations; the UI reloads to fetch the real values.)

### 3. `webapp/src/app/songsets/page.tsx`

Serialize `failedAt` in the server component map:

```typescript
songsets: result.songsets.map((songset) => ({
  ...songset,
  createdAt: songset.createdAt.toISOString(),
  updatedAt: songset.updatedAt.toISOString(),
  failedAt: songset.failedAt?.toISOString() ?? null,
})),
```

### 4. `webapp/src/app/songsets/SongsetsClient.tsx`

Add to `ApiSongset`:

```typescript
renderErrorMessage: string | null;
failedAt: string | null;
```

Add to `transformSongsets`:

```typescript
renderErrorMessage: songset.renderErrorMessage,
failedAt: songset.failedAt ? new Date(songset.failedAt) : null,
```

### 5. `webapp/src/components/songset/SongsetList.tsx`

Add to `Songset` interface:

```typescript
renderErrorMessage?: string | null;
failedAt?: Date | null;
```

No other change needed; `SongsetRow` is rendered with `{...songset}`.

### 6. `webapp/src/components/songset/SongsetRow.tsx`

Add to `SongsetRowProps`:

```typescript
renderErrorMessage?: string | null;
failedAt?: Date | null;
```

Destructure them and pass into `RenderStatusBadge`:

```tsx
<RenderStatusBadge
  state={renderState}
  errorMessage={renderErrorMessage}
  failedAt={failedAt}
/>
```

### 7. `webapp/src/app/songsets/[id]/page.tsx`

Add to `initialData`:

```typescript
renderErrorMessage: songset.renderErrorMessage,
failedAt: songset.failedAt?.toISOString() ?? null,
```

### 8. `webapp/src/app/songsets/[id]/SongsetEditorClient.tsx`

Add to `ApiSongset`:

```typescript
renderErrorMessage: string | null;
failedAt: string | null;
```

Add to `ApiResponse`:

```typescript
renderErrorMessage: string | null;
failedAt: string | null;
```

State initialization:

```typescript
renderErrorMessage: initialData.renderErrorMessage,
failedAt: initialData.failedAt ? new Date(initialData.failedAt) : null,
```

### 9. `webapp/src/components/songset/SongsetEditor.tsx`

Add to the `songset` prop shape:

```typescript
renderErrorMessage?: string | null;
failedAt?: Date | null;
```

Pass fields to `RenderStatusBadge`:

```tsx
<RenderStatusBadge
  state={songset.renderState}
  errorMessage={songset.renderErrorMessage}
  failedAt={songset.failedAt}
/>
```

Add an inline failure alert below the app bar:

```tsx
{songset.renderState === "failed" && (
  <Alert variant="destructive" className="rounded-none border-x-0">
    <AlertCircle className="size-4" />
    <AlertTitle>Render failed</AlertTitle>
    <AlertDescription className="flex items-center gap-2 flex-wrap">
      <span>
        {songset.renderErrorMessage?.trim()
          ? songset.renderErrorMessage
          : songset.failedAt
            ? `Render failed at ${formatRenderFailedAt(songset.failedAt)}`
            : "Render failed"}
      </span>
      <Button size="sm" variant="outline" onClick={onRender}>
        Render again
      </Button>
    </AlertDescription>
  </Alert>
)}
```

### 10. `webapp/src/components/songset/RenderStatusBadge.tsx`

Import the tooltip components and the helper:

```typescript
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { formatRenderFailedAt } from "@/lib/render/error-message";
```

Extend props:

```typescript
interface RenderStatusBadgeProps {
  state: RenderState;
  errorMessage?: string | null;
  failedAt?: Date | null;
  className?: string;
}
```

Render only for `failed`:

```tsx
if (state === "failed") {
  const tooltipText = errorMessage?.trim()
    ? errorMessage
    : failedAt
      ? `Render failed at ${formatRenderFailedAt(failedAt)}`
      : null;

  if (tooltipText) {
    return (
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>{badge}</TooltipTrigger>
          <TooltipContent className="max-w-80 whitespace-pre-wrap break-words">
            <p>{tooltipText}</p>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    );
  }
}

return badge;
```

## Files to Modify (Tests)

### 11. `webapp/src/test/components/songset/RenderStatusBadge.test.tsx`

Import `userEvent` and add hover-based tests:

```typescript
import userEvent from "@testing-library/user-event";

describe("failed state tooltip", () => {
  it("shows error message tooltip on hover", async () => {
    const user = userEvent.setup();
    render(<RenderStatusBadge state="failed" errorMessage="FFmpeg encoding failed: exit code 1" />);

    await user.hover(screen.getByText("Render failed"));

    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "FFmpeg encoding failed: exit code 1"
    );

    await user.unhover(screen.getByText("Render failed"));
  });

  it("shows fallback datetime when errorMessage is null", async () => {
    const user = userEvent.setup();
    render(
      <RenderStatusBadge
        state="failed"
        errorMessage={null}
        failedAt={new Date("2026-06-18T15:45:00Z")}
      />
    );

    await user.hover(screen.getByText("Render failed"));

    expect(await screen.findByRole("tooltip")).toHaveTextContent(/Render failed at/);

    await user.unhover(screen.getByText("Render failed"));
  });

  it("shows fallback datetime when errorMessage is whitespace", async () => {
    const user = userEvent.setup();
    render(
      <RenderStatusBadge
        state="failed"
        errorMessage="   "
        failedAt={new Date("2026-06-18T15:45:00Z")}
      />
    );

    await user.hover(screen.getByText("Render failed"));

    expect(await screen.findByRole("tooltip")).toHaveTextContent(/Render failed at/);

    await user.unhover(screen.getByText("Render failed"));
  });

  it("shows no tooltip when both errorMessage and failedAt are null", async () => {
    const user = userEvent.setup();
    render(<RenderStatusBadge state="failed" />);

    await user.hover(screen.getByText("Render failed"));

    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();

    await user.unhover(screen.getByText("Render failed"));
  });
});

describe("non-failed states", () => {
  it("does not show tooltip for fresh state even with errorMessage", async () => {
    const user = userEvent.setup();
    render(<RenderStatusBadge state="fresh" errorMessage="some error" />);

    await user.hover(screen.getByText("Rendered"));

    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });
});
```

### 12. `webapp/src/test/components/songset/SongsetRow.test.tsx`

Add fields to `defaultProps`:

```typescript
renderErrorMessage: null,
failedAt: null,
```

Add hover-based tooltip tests:

```typescript
describe("render failed error tooltip", () => {
  it("shows error tooltip on hover when renderState is failed", async () => {
    const user = userEvent.setup();
    renderRow({
      renderState: "failed" as RenderState,
      renderErrorMessage: "FFmpeg encoding failed",
      failedAt: new Date("2026-06-18T15:45:00Z"),
    });

    await user.hover(screen.getByText("Render failed"));

    expect(await screen.findByRole("tooltip")).toHaveTextContent("FFmpeg encoding failed");

    await user.unhover(screen.getByText("Render failed"));
  });

  it("shows fallback datetime tooltip on hover when errorMessage is null", async () => {
    const user = userEvent.setup();
    renderRow({
      renderState: "failed" as RenderState,
      renderErrorMessage: null,
      failedAt: new Date("2026-06-18T15:45:00Z"),
    });

    await user.hover(screen.getByText("Render failed"));

    expect(await screen.findByRole("tooltip")).toHaveTextContent(/Render failed at/);

    await user.unhover(screen.getByText("Render failed"));
  });
});
```

### 13. `webapp/src/test/components/songset/SongsetEditor.test.tsx`

Add to `mockSongset`:

```typescript
renderErrorMessage: null,
failedAt: null,
```

Add inline-alert and tooltip tests:

```typescript
describe("render failed error display", () => {
  it("shows sanitized error message in the inline alert", () => {
    renderEditor({
      songset: {
        ...mockSongset,
        renderState: "failed" as RenderState,
        renderErrorMessage: "FFmpeg encoding failed",
        failedAt: new Date("2026-06-18T15:45:00Z"),
      },
    });

    expect(screen.getByText("FFmpeg encoding failed")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /render again/i })).toBeInTheDocument();
  });

  it("shows fallback datetime in the inline alert when no error message", () => {
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

  it("shows error tooltip on the failed badge", async () => {
    const user = userEvent.setup();
    renderEditor({
      songset: {
        ...mockSongset,
        renderState: "failed" as RenderState,
        renderErrorMessage: "FFmpeg encoding failed",
        failedAt: new Date("2026-06-18T15:45:00Z"),
      },
    });

    await user.hover(screen.getByText("Render failed"));

    expect(await screen.findByRole("tooltip")).toHaveTextContent("FFmpeg encoding failed");

    await user.unhover(screen.getByText("Render failed"));
  });
});
```

### 14. `webapp/src/test/api/songsets/db.test.ts`

Update existing mock rows for `listSongsetSummaries` to include:

```typescript
renderErrorMessage: null,
latestJobStartedAt: null,
latestJobCreatedAt: null,
```

Add new test:

```typescript
it("returns sanitized renderErrorMessage and failedAt from latest render job", async () => {
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
      latestJobStartedAt: new Date("2024-01-02T11:00:00Z"),
      latestJobCreatedAt: new Date("2024-01-02T10:00:00Z"),
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
  expect(result.songsets[0].failedAt).toEqual(new Date("2024-01-02T11:00:00Z"));
});
```

Also add `renderErrorMessage`, `latestJobStartedAt`, `latestJobCreatedAt` to the `getSongsetEditorData` mock rows so newer tests can assert the same fields.

## Files NOT Modified

- `webapp/src/app/api/songsets/route.ts` — GET returns `listSongsetSummaries` result directly; new fields are auto-included.
- `webapp/src/app/api/songsets/[id]/route.ts` — PATCH returns `updateSongset`; new null fields are auto-included.
- `webapp/src/app/api/songsets/[id]/duplicate/route.ts` — returns `duplicateSongset`; new null fields auto-included.
- `webapp/src/db/schema.ts` — required columns already exist.
- `webapp/src/app/layout.tsx` — no global `TooltipProvider`; each tooltip wraps locally.

## Verification

```bash
cd webapp && pnpm lint
cd webapp && pnpm build
cd webapp && pnpm test -- --run src/test/components/songset/RenderStatusBadge.test.tsx
cd webapp && pnpm test -- --run src/test/components/songset/SongsetRow.test.tsx
cd webapp && pnpm test -- --run src/test/components/songset/SongsetEditor.test.tsx
cd webapp && pnpm test -- --run src/test/api/songsets/db.test.ts
```
