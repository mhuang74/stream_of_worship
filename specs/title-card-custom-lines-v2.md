# Title Card: Custom Lines of Text (v2)

## Problem

The title card currently renders a hardcoded layout: one heading line (derived from the first song's title) and one subtitle line (`{count} 首歌曲 · {mm:ss}`). Users cannot customize what text appears on the title card.

The desired behavior is:
1. Users can optionally provide custom lines of text for the title card
2. Each line is rendered as a separate line, centered both vertically and horizontally
3. When no custom lines are given, the default is: **songset name** as the first line, followed by **all song titles** from the songset
4. The first line (heading) renders 20 pts larger than the remaining lines
5. Font size auto-scales so all lines fit on screen

---

## Current State

### Data flow

```
RenderForm (browser)
  includeTitleCard: boolean
  titleCardDurationSeconds: number
       |
       v
POST /api/render-jobs  (Zod validation)
       |
       v
job-manager.createRenderJob()  →  INSERT INTO render_jobs
       |
       v
SQS → Lambda → pipeline.execute_render_pipeline()
       |
       v
VideoEngine(include_title_card, title_card_duration_seconds)
       |
       v
TitleCardConfig(songset_name=segments[0].item.song_title, song_count, total_duration_seconds)
       |
       v
FrameRenderer.render_title_card(config)
  → heading at 40% height (2x base font size)
  → subtitle "{count} 首歌曲 · {mm:ss}" at 55% height (1x base font size)
```

### Key files

| File | Lines | Role |
|------|-------|------|
| `webapp/src/components/render/RenderForm.tsx` | 24-33, 242-280 | Form UI: checkbox + duration selector |
| `webapp/src/app/songsets/[id]/render/page.tsx` | 84-94, 137-173 | Render page: fetches songset, submits form |
| `webapp/src/app/api/render-jobs/route.ts` | 7-16 | Zod schema for POST body |
| `webapp/src/lib/render/job-manager.ts` | 112-155 | Creates render job in DB |
| `webapp/src/db/schema.ts` | 204-247 | `render_jobs` table schema |
| `services/render-worker/src/sow_render_worker/db.py` | 49-50 | `RenderJob` dataclass |
| `services/render-worker/src/sow_render_worker/pipeline.py` | 347-354 | Wires job params to VideoEngine |
| `services/render-worker/src/sow_render_worker/video_engine.py` | 63-82, 202-212 | VideoEngine constructor + TitleCardConfig construction |
| `services/render-worker/src/sow_render_worker/frame_renderer.py` | 47-53, 420-453 | `TitleCardConfig` dataclass + `render_title_card()` |

### Current `TitleCardConfig`

```python
@dataclass(frozen=True)
class TitleCardConfig:
    enabled: bool
    duration_seconds: float
    songset_name: str
    song_count: int
    total_duration_seconds: float
```

### Current `render_title_card()` output

- Solid background from template
- `config.songset_name` centered at 40% height, font size = `base_font_size * 2` (auto-fitted to width)
- Subtitle `"{song_count} 首歌曲 · {duration_text}"` centered at 55% height, font size = `base_font_size`

### Current `RenderFormData` (TypeScript)

```typescript
export interface RenderFormData {
  audioEnabled: boolean
  videoEnabled: boolean
  template: "dark" | "gradient_warm" | "gradient_blue"
  resolution: "720p" | "1080p"
  fontSizePreset: "S" | "M" | "L" | "XL"
  includeTitleCard: boolean
  titleCardDurationSeconds: number
  offlineEnabled: boolean
}
```

---

## Proposed Changes

### 1. `TitleCardConfig` — replace `songset_name`/`song_count` with `lines`

**File:** `services/render-worker/src/sow_render_worker/frame_renderer.py` (lines 47-53)

```python
# Before:
@dataclass(frozen=True)
class TitleCardConfig:
    enabled: bool
    duration_seconds: float
    songset_name: str
    song_count: int
    total_duration_seconds: float

# After:
@dataclass(frozen=True)
class TitleCardConfig:
    enabled: bool
    duration_seconds: float
    lines: tuple[str, ...]
    total_duration_seconds: float
```

- `lines` is a tuple of strings, each rendered as a separate line
- Empty tuple means title card is effectively blank (shouldn't happen in practice — see defaults below)
- Using `tuple` instead of `list` for immutability (matching the `frozen=True` dataclass)

### 2. `render_title_card()` — multi-line centered rendering with auto-scaling

**File:** `services/render-worker/src/sow_render_worker/frame_renderer.py` (lines 420-453)

New rendering logic:

1. **Heading font size**: Start with `base_font_size * 2` (same as current heading), auto-fit each line's width to screen
2. **Body font size**: `heading_font_size - 20` (heading is 20 pts larger than body)
3. **Auto-scale**: If all lines don't fit vertically within the screen height (with margins), reduce both heading and body font sizes proportionally until they fit. Minimum body font size = 16px; if lines still don't fit, render at minimum size (lines beyond screen height are not rendered).
4. **Vertical centering**: Calculate total text block height (sum of line heights + inter-line spacing), then center the block vertically on screen
5. **Horizontal centering**: Each line is individually centered horizontally (anchor="mt")
6. **Inter-line spacing**: 1.2× the body font size between lines; 1.5× the body font size between the heading (first line) and the second line

**Note on vertical centering accuracy**: The auto-scale loop calculates `total_height` using target font sizes, but `fit_text()` may reduce font sizes for long lines during rendering, making actual rendered heights slightly smaller than calculated. This results in minor vertical centering inaccuracy (a few pixels), which is imperceptible on a title card and acceptable.

Pseudocode:

```python
def render_title_card(self, config: TitleCardConfig) -> Image.Image:
    width, height = self.resolution
    img = Image.new("RGBA", (width, height), (*self.template.background_color, 255))
    draw = ImageDraw.Draw(img)
    text_r, text_g, text_b = self.template.text_color

    if not config.lines:
        return img

    margin = 40  # horizontal margin in px
    min_body_font_size = 16
    line_spacing_factor = 1.2
    heading_gap_factor = 1.5

    # Start with target sizes
    heading_font_size_target = self.base_font_size * 2
    body_font_size_target = heading_font_size_target - 20

    # Auto-fit: reduce font sizes until all lines fit vertically
    heading_font_size = heading_font_size_target
    body_font_size = body_font_size_target

    while True:
        heading_font = self._get_font(heading_font_size)
        body_font = self._get_font(body_font_size)

        # Calculate total block height
        total_height = 0
        for i, line in enumerate(config.lines):
            font = heading_font if i == 0 else body_font
            bbox = draw.textbbox((0, 0), line, font=font)
            line_height = bbox[3] - bbox[1]
            total_height += line_height
            if i == 0 and len(config.lines) > 1:
                total_height += int(body_font_size * heading_gap_factor)
            elif i > 0:
                total_height += int(body_font_size * line_spacing_factor)

        if total_height <= height - margin * 2 or body_font_size <= min_body_font_size:
            break

        heading_font_size -= 2
        body_font_size = max(min_body_font_size, heading_font_size - 20)

    # Render lines centered vertically and horizontally
    y_start = (height - total_height) // 2
    current_y = y_start

    for i, line in enumerate(config.lines):
        font = heading_font if i == 0 else body_font
        # Auto-fit line width
        fitted_size = self.fit_text(draw, line, heading_font_size if i == 0 else body_font_size, width - margin * 2)
        font = self._get_font(fitted_size)
        draw.text(
            (width // 2, current_y),
            line,
            fill=(text_r, text_g, text_b),
            font=font,
            anchor="mt",
        )
        bbox = draw.textbbox((0, 0), line, font=font)
        line_height = bbox[3] - bbox[1]
        current_y += line_height
        if i == 0 and len(config.lines) > 1:
            current_y += int(body_font_size * heading_gap_factor)
        elif i > 0:
            current_y += int(body_font_size * line_spacing_factor)

    return img
```

### 3. `VideoEngine` — accept `title_card_lines` parameter

**File:** `services/render-worker/src/sow_render_worker/video_engine.py`

#### 3a. Constructor (lines 63-82)

Add `title_card_lines: list[str] | None = None` parameter:

```python
def __init__(
    self,
    asset_fetcher: AssetFetcherProtocol,
    template: VideoTemplateName = "dark",
    font_size_preset: FontSizePreset = "M",
    resolution: str = "1080p",
    fps: int = 24,
    include_title_card: bool = True,
    title_card_duration_seconds: float = 5.0,
    title_card_lines: list[str] | None = None,  # NEW
    ffmpeg_path: str | None = None,
    ffprobe_path: str | None = None,
):
    # ... existing assignments ...
    self.title_card_lines = title_card_lines  # NEW
```

#### 3b. `generate_video()` — construct `TitleCardConfig` with lines (lines 202-212)

Replace the current `TitleCardConfig` construction:

```python
# Before:
title_card_config = TitleCardConfig(
    enabled=True,
    duration_seconds=total_duration_seconds,
    songset_name=(segments[0].item.song_title or "Worship Set") if segments else "Worship Set",
    song_count=len(segments),
    total_duration_seconds=total_duration_seconds,
)

# After:
# Normalize: treat empty list same as None (use defaults)
if self.title_card_lines:
    # User-provided custom lines (non-empty)
    title_lines = tuple(self.title_card_lines)
else:
    # Default: songset name + all song titles
    song_titles = [seg.item.song_title for seg in segments if seg.item.song_title]
    display_name = self.songset_name or "Worship Set"
    title_lines = tuple([display_name] + song_titles) if song_titles else (display_name,)

title_card_config = TitleCardConfig(
    enabled=True,
    duration_seconds=total_duration_seconds,
    lines=title_lines,
    total_duration_seconds=total_duration_seconds,
)
```

**Key change from v1**: The guard is `if self.title_card_lines:` (truthy check) instead of `if self.title_card_lines is not None:`. This treats empty list `[]` the same as `None`, ensuring both fall back to defaults.

**Problem:** The `songset_name` (the user-given name of the songset, e.g. "Sunday Morning Worship") is not currently available in the VideoEngine. It only has access to `segments` (which have `song_title` per segment).

**Solution:** Pass `songset_name` as a new constructor parameter to `VideoEngine`. The pipeline already has access to the songset's `name` field via the database.

### 4. `VideoEngine` — accept `songset_name` parameter

**File:** `services/render-worker/src/sow_render_worker/video_engine.py`

#### 4a. Constructor

Add `songset_name: str = ""` parameter:

```python
def __init__(
    self,
    asset_fetcher: AssetFetcherProtocol,
    template: VideoTemplateName = "dark",
    font_size_preset: FontSizePreset = "M",
    resolution: str = "1080p",
    fps: int = 24,
    include_title_card: bool = True,
    title_card_duration_seconds: float = 5.0,
    title_card_lines: list[str] | None = None,
    songset_name: str = "",  # NEW
    ffmpeg_path: str | None = None,
    ffprobe_path: str | None = None,
):
    # ... existing assignments ...
    self.songset_name = songset_name  # NEW
```

### 5. Pipeline — fetch songset name and pass to VideoEngine

**File:** `services/render-worker/src/sow_render_worker/pipeline.py`

#### 5a. Extend `fetch_songset_items()` to return songset name (around line 99)

Instead of adding a separate `fetch_songset_name()` function (which would be an extra DB roundtrip), extend the existing query to also return the songset name:

```python
def fetch_songset_items(
    conn: psycopg2.extensions.connection,
    songset_id: str,
) -> tuple[str, list[SongsetItem]]:
    """
    Returns: (songset_name, list of SongsetItem)
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        # First, get the songset name
        cur.execute(
            "SELECT name FROM songsets WHERE id = %s",
            (songset_id,),
        )
        songset_row = cur.fetchone()
        songset_name = songset_row["name"] if songset_row else "Worship Set"
        
        # Then, fetch songset items (existing query)
        cur.execute(
            """
            SELECT 
                si.id,
                si.song_id,
                si.recording_hash_prefix,
                si.position,
                si.gap_beats,
                si.crossfade_start_beats,
                si.crossfade_end_beats,
                si.tempo_bpm,
                si.duration_seconds,
                s.title as song_title
            FROM songset_items si
            LEFT JOIN recordings r ON si.recording_hash_prefix = r.hash_prefix
            LEFT JOIN songs s ON si.song_id = s.id
            WHERE si.songset_id = %s
            ORDER BY si.position
            """,
            (songset_id,),
        )
        # ... existing row-to-SongsetItem mapping ...
        
    return songset_name, items
```

#### 5b. Pass to VideoEngine (lines 347-354)

```python
# Before:
video_engine = VideoEngine(
    asset_fetcher,
    template=job.template,
    font_size_preset=job.font_size_preset,
    resolution=job.resolution,
    include_title_card=job.include_title_card,
    title_card_duration_seconds=job.title_card_duration_seconds or 5.0,
)

# After:
songset_name, songset_items = fetch_songset_items(conn, job.songset_id)
# Normalize: treat empty list same as None
title_card_lines = job.title_card_lines if job.title_card_lines else None

video_engine = VideoEngine(
    asset_fetcher,
    template=job.template,
    font_size_preset=job.font_size_preset,
    resolution=job.resolution,
    include_title_card=job.include_title_card,
    title_card_duration_seconds=job.title_card_duration_seconds or 5.0,
    title_card_lines=title_card_lines,
    songset_name=songset_name,
)
```

**Note on `title_card_duration_seconds`**: The fallback `or 5.0` is inconsistent with the DB default (10) and form default (10). This is a pre-existing issue. The form always sends a value (defaulting to 10), so the fallback only affects programmatic API calls that omit the field. Consider aligning these defaults in a future cleanup.

### 6. `RenderJob` dataclass — add `title_card_lines` field

**File:** `services/render-worker/src/sow_render_worker/db.py` (lines 49-50)

```python
# Add after line 50:
title_card_lines: Optional[list[str]] = None
```

Update `_row_to_render_job()` (around line 102) to parse the new column:

```python
import json  # Add at top of file

def _row_to_render_job(row: dict[str, Any]) -> RenderJob:
    # Parse JSON-encoded title_card_lines from DB
    title_card_lines_raw = row.get("title_card_lines")
    title_card_lines = None
    if title_card_lines_raw:
        try:
            parsed = json.loads(title_card_lines_raw)
            # Normalize: treat empty list same as None
            title_card_lines = parsed if parsed else None
        except json.JSONDecodeError:
            # Log warning but don't fail the job
            title_card_lines = None
    
    return RenderJob(
        # ... existing fields ...
        title_card_lines=title_card_lines,
    )
```

**Key change from v1**: JSON parsing is done in `_row_to_render_job()`, and empty lists are normalized to `None`.

### 7. Database schema — add `title_card_lines` column

**File:** `webapp/src/db/schema.ts` (after line 237)

```typescript
titleCardLines: text("title_card_lines"),  // JSON array of strings, nullable
```

The column stores a JSON-encoded array of strings (e.g. `'["Sunday Worship","Amazing Grace","How Great Thou Art"]'`). Using `text` type with JSON encoding avoids adding a PostgreSQL `jsonb` column, keeping the migration simple.

**Migration:** `npx drizzle-kit generate` then `npx drizzle-kit push` (or migrate).

### 8. API route — accept `titleCardLines` in POST body

**File:** `webapp/src/app/api/render-jobs/route.ts` (line 7-16)

Add to Zod schema:

```typescript
titleCardLines: z.array(z.string().min(1).max(200)).min(1).max(20).optional(),
```

- Each line: 1-200 characters (no empty strings)
- Min 1 line, max 20 lines
- Optional: when omitted, the render worker uses the default (songset name + song titles)

**Key change from v1**: Changed `.min(0)` to `.min(1)`. Since empty arrays are normalized to `undefined` at the API layer (see job-manager below), there's no reason to accept `[]`.

### 9. Job manager — persist `titleCardLines`

**File:** `webapp/src/lib/render/job-manager.ts` (lines 129-155)

Add to the INSERT values:

```typescript
titleCardLines: input.titleCardLines && input.titleCardLines.length > 0 
  ? JSON.stringify(input.titleCardLines) 
  : null,
```

**Key change from v1**: Explicitly store `null` for empty arrays, not `"[]"`. This ensures the render worker receives `None` (not `[]`) when no custom lines are provided.

Add to the `CreateRenderJobInput` type and the `RenderJob` return type mapping.

### 10. `RenderFormData` — add `titleCardLines` field

**File:** `webapp/src/components/render/RenderForm.tsx` (lines 24-33)

```typescript
export interface RenderFormData {
  audioEnabled: boolean
  videoEnabled: boolean
  template: "dark" | "gradient_warm" | "gradient_blue"
  resolution: "720p" | "1080p"
  fontSizePreset: "S" | "M" | "L" | "XL"
  includeTitleCard: boolean
  titleCardDurationSeconds: number
  titleCardLines: string[]  // NEW
  offlineEnabled: boolean
}
```

Default value: `[]` (empty array = use default song titles).

### 11. RenderForm UI — add textarea for custom title card lines

**File:** `webapp/src/components/render/RenderForm.tsx` (inside the Title Card card, lines 259-280)

When `includeTitleCard` is checked, show a `<textarea>` below the duration selector:

```tsx
{formData.includeTitleCard && (
  <div className="space-y-2 pl-6">
    <Label htmlFor="titleCardDuration">Duration</Label>
    {/* ... existing duration selector ... */}

    <div className="space-y-2 pt-2">
      <Label htmlFor="titleCardLines">Custom title card text</Label>
      <p className="text-sm text-muted-foreground">
        One line per entry. Leave empty to use songset name and song titles.
      </p>
      <textarea
        id="titleCardLines"
        className="flex min-h-[100px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
        placeholder={"Sunday Morning Worship\nAmazing Grace\nHow Great Thou Art"}
        value={formData.titleCardLines.join("\n")}
        onChange={(e) => {
          const lines = e.target.value.split("\n").filter((line) => line.trim() !== "")
          updateField("titleCardLines", lines)
        }}
      />
    </div>
  </div>
)}
```

**Behavior:**
- Empty textarea → `titleCardLines = []` → API receives `undefined` → DB stores `null` → render worker uses default (songset name + song titles)
- User types lines → `titleCardLines = ["line1", "line2", ...]` → API receives the array → DB stores JSON → render worker uses these exact lines
- Blank lines are filtered out (`.filter(line => line.trim() !== "")`)
- Each non-empty line becomes one centered line on the title card

**Note on blank line filtering**: Blank lines are intentionally removed because a centered multi-line layout makes blank spacing lines meaningless. If users want visual spacing, they should adjust the font size preset instead.

### 12. Render page — pass song titles to RenderForm for preview

**File:** `webapp/src/app/songsets/[id]/render/page.tsx`

#### 12a. Extend `SongsetData` to include song titles (lines 26-32)

```typescript
interface SongsetData {
  id: string
  name: string
  description: string | null
  markedLineCount: number
  renderState: RenderState
  songTitles: string[]  // NEW
}
```

#### 12b. Extract song titles from API response (lines 84-94)

```typescript
setSongset({
  id: data.id,
  name: data.name,
  description: data.description,
  markedLineCount: data.items?.reduce(
    (sum: number, item: { markedLineCount?: number }) =>
      sum + (item.markedLineCount || 0),
    0
  ) || 0,
  renderState,
  songTitles: data.items?.map((item: { song?: { title: string } | null }) =>
    item.song?.title ?? "Unknown Song"
  ) ?? [],
})
```

#### 12c. Pass `songTitles` and `songsetName` to `RenderForm` (lines 261-267)

```tsx
<RenderForm
  songsetId={songsetId}
  markedLineCount={songset.markedLineCount}
  songsetName={songset.name}  // NEW
  songTitles={songset.songTitles}  // NEW
  initialData={initialData}
  onSubmit={handleSubmit}
  onCancel={() => router.push(`/songsets/${songsetId}`)}
/>
```

#### 12d. Update `RenderFormProps` (lines 35-42)

```typescript
interface RenderFormProps {
  songsetId: string
  initialData?: Partial<RenderFormData>
  markedLineCount?: number
  songsetName?: string  // NEW
  songTitles?: string[]  // NEW
  onSubmit: (data: RenderFormData) => void
  onCancel: () => void
  isSubmitting?: boolean
}
```

#### 12e. Show default lines preview in RenderForm

Below the textarea, when `titleCardLines` is empty and `songTitles` is provided, show a muted preview of what the default title card will look like:

```tsx
{formData.titleCardLines.length === 0 && songTitles && songTitles.length > 0 && (
  <div className="rounded-md border border-dashed border-muted-foreground/25 bg-muted/50 p-3">
    <p className="text-xs text-muted-foreground mb-1">Default title card lines:</p>
    <p className="text-sm text-muted-foreground">
      {songsetName || "Worship Set"}<br />
      {songTitles.join("\n")}
    </p>
  </div>
)}
```

### 13. Render page — include `titleCardLines` in POST body

**File:** `webapp/src/app/songsets/[id]/render/page.tsx` (lines 140-152)

```typescript
body: JSON.stringify({
  songsetId,
  template: formData.template,
  resolution: formData.resolution,
  audioEnabled: formData.audioEnabled,
  videoEnabled: formData.videoEnabled,
  fontSizePreset: formData.fontSizePreset,
  includeTitleCard: formData.includeTitleCard,
  titleCardDurationSeconds: formData.titleCardDurationSeconds,
  titleCardLines: formData.titleCardLines.length > 0 ? formData.titleCardLines : undefined,
}),
```

When `titleCardLines` is empty, send `undefined` (omitted from JSON) so the API treats it as "use defaults".

### 14. Render page — restore `titleCardLines` from previous job

**File:** `webapp/src/app/songsets/[id]/render/page.tsx` (lines 105-113)

When loading initial data from a previous completed job:

```typescript
setInitialData({
  template: job.template as RenderFormData["template"],
  resolution: job.resolution as RenderFormData["resolution"],
  audioEnabled: job.audioEnabled,
  videoEnabled: job.videoEnabled,
  fontSizePreset: job.fontSizePreset as RenderFormData["fontSizePreset"],
  includeTitleCard: job.includeTitleCard,
  titleCardDurationSeconds: job.titleCardDurationSeconds,
  titleCardLines: job.titleCardLines ?? [],  // NEW
})
```

---

## Implementation Order

1. **Database**: Add `title_card_lines` column to `render_jobs` table (schema + migration)
2. **Render worker — data model**: Update `RenderJob` dataclass, `_row_to_render_job()` with JSON parsing, `TitleCardConfig`
3. **Render worker — rendering**: Rewrite `render_title_card()` with multi-line auto-scaling logic
4. **Render worker — VideoEngine**: Add `title_card_lines` and `songset_name` params, update `generate_video()`
5. **Render worker — pipeline**: Extend `fetch_songset_items()` to return songset name, wire new params to VideoEngine
6. **Render worker — tests**: Add/update tests for new `render_title_card()` behavior
7. **Webapp — API**: Update Zod schema, job-manager INSERT/mapping
8. **Webapp — UI**: Update `RenderFormData`, `RenderForm` (textarea + preview), render page (song titles extraction + POST body)
9. **Webapp — tests**: Update any existing render form tests
10. **Integration test**: End-to-end render with custom title card lines

---

## Edge Cases

### Empty `titleCardLines` (default behavior)

When `titleCardLines` is `None` (Python) / `undefined` (API) / `[]` (form), the render worker constructs defaults:
- First line: songset name (from `songsets.name`), or "Worship Set" if empty
- Remaining lines: all song titles from the songset items (in order)

If the songset has no songs (empty songset), fall back to `("Worship Set",)`.

### Very long lines

The existing `fit_text()` method already handles horizontal overflow by reducing font size until the text fits within the screen width minus margins. This continues to work per-line.

### Many lines (10+ songs)

Auto-scaling reduces font sizes until all lines fit vertically. If body font size hits the minimum (16px) and lines still don't fit, they render at minimum size. Lines beyond the screen height are simply not rendered (the `current_y` advances past the bottom). This is unlikely in practice — even 15 lines at 16px body / 36px heading fit within 1080p height.

### Single line

If only one line is provided, it renders as the heading (20 pts larger than body, but body is never used). The line is centered both vertically and horizontally.

### Lines with only whitespace

The textarea filters out blank/whitespace-only lines before submitting. The render worker also normalizes empty strings during JSON parsing as a safety measure.

### Unicode / CJK characters

The existing font rendering handles CJK characters (Chinese song titles). No change needed — PIL/Pillow renders whatever the font supports.

### Empty songset name

If the songset has no name (empty string or null in DB), the default lines use "Worship Set" as the heading instead of a blank line.

---

## Verification Checklist

- [ ] `title_card_lines` column added to `render_jobs` table
- [ ] `TitleCardConfig` uses `lines: tuple[str, ...]` instead of `songset_name`/`song_count`
- [ ] `render_title_card()` renders multiple lines centered vertically and horizontally
- [ ] First line (heading) is 20 pts larger than remaining lines
- [ ] Font size auto-scales to fit all lines on screen
- [ ] Default lines = [songset name, ...song titles] when no custom lines provided
- [ ] Empty songset name falls back to "Worship Set"
- [ ] `VideoEngine` accepts `title_card_lines` and `songset_name` params
- [ ] `VideoEngine.generate_video()` treats empty list same as None (uses defaults)
- [ ] Pipeline fetches songset name via extended `fetch_songset_items()`
- [ ] Pipeline passes songset name + title_card_lines to VideoEngine
- [ ] Pipeline normalizes empty list to None before passing to VideoEngine
- [ ] `_row_to_render_job()` parses JSON-encoded `title_card_lines` from DB
- [ ] `_row_to_render_job()` normalizes empty list to None
- [ ] API route validates `titleCardLines` (array of strings, min 1, max 20, max 200 chars each)
- [ ] Job manager persists `titleCardLines` as JSON in DB, or `null` for empty array
- [ ] `RenderFormData` includes `titleCardLines: string[]`
- [ ] RenderForm shows textarea when title card is enabled
- [ ] RenderForm shows default lines preview when textarea is empty
- [ ] Render page extracts song titles from API response
- [ ] Render page includes `titleCardLines` in POST body (undefined if empty)
- [ ] Previous job's `titleCardLines` restored in initial form data
- [ ] Render worker tests pass for new `render_title_card()` behavior
- [ ] End-to-end render with custom lines produces correct video
- [ ] End-to-end render with empty custom lines uses defaults

---

## Changes from v1

1. **Empty array normalization (both layers)**: 
   - API/job-manager stores `null` for empty arrays (not `"[]"`)
   - Render worker `_row_to_render_job()` normalizes empty list to `None`
   - Pipeline normalizes empty list to `None` before passing to VideoEngine
   - VideoEngine uses truthy check (`if self.title_card_lines:`) instead of `is not None`

2. **JSON parsing in `_row_to_render_job()`**: 
   - Added `import json` and proper parsing with error handling
   - Empty list normalized to `None`

3. **Empty songset name fallback**: 
   - `fetch_songset_items()` returns "Worship Set" as fallback
   - `VideoEngine.generate_video()` uses `songset_name or "Worship Set"`

4. **Removed truncation mention**: The edge case section no longer claims truncation is implemented. Lines render at minimum font size; overflow is simply not visible.

5. **Vertical centering accuracy note**: Documented that minor inaccuracy from `fit_text` is accepted.

6. **Combined `fetch_songset_name` with `fetch_songset_items`**: Single DB roundtrip instead of two.

7. **Zod schema `.min(1)`**: Changed from `.min(0)` since empty arrays are normalized to `undefined`.

8. **Blank line filtering note**: Added explanation that blank lines are intentionally removed for centered layouts.
