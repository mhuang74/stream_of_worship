# Fix: Chapter Skip Buttons Not Working — snake_case vs camelCase Mismatch (v2)

> **Status:** Implementation plan ready.
> **Supersedes:** `fix-chapter-skip-snake-case-camelcase-mismatch.md` (v1)
> **Updated:** 2026-05-26

---

## Problem

The "Chapter Skip Forward/Backwards" buttons on the Worship Screen (ControllerPlayer) do nothing when clicked. Console shows:

```
ControllerPlayer.tsx:177 Uncaught TypeError: Failed to set the 'currentTime' property on 'HTMLMediaElement': The provided double value is non-finite.
    at ControllerPlayer.useCallback[handleSeek] (ControllerPlayer.tsx:177:24)
    at ControllerPlayer.useCallback[handleNextSong] (ControllerPlayer.tsx:201:7)
```

After adding `isFinite()` guards, the error disappears but buttons still do nothing.

---

## Root Cause Analysis

### The Mismatch

The backend serializes chapters using `dataclasses.asdict()` which preserves Python **snake_case** field names, but the frontend expects **camelCase** keys.

| Python dataclass field | `asdict()` JSON key | Frontend expected key | Match? |
|---|---|---|---|
| `position` | `"position"` | `position` | YES |
| `song_title` | `"song_title"` | `songTitle` | **NO** |
| `start_seconds` | `"start_seconds"` | `startSeconds` | **NO** |
| `end_seconds` | `"end_seconds"` | `endSeconds` | **NO** |
| `ChapterLine.start_seconds` | `"start_seconds"` | `startSeconds` | **NO** |
| `total_duration_seconds` | `"total_duration_seconds"` | `totalDurationSeconds` | **NO** |
| `generated_at` | `"generated_at"` | `generatedAt` | **NO** |

### Data Flow

```
Python dataclass (snake_case fields)
    |
    v  dataclasses.asdict()  <-- BUG: preserves snake_case
    |
JSON with snake_case keys: {"song_title", "start_seconds", ...}
    |
    v  boto3 put_object to R2
    |
R2: renders/{jobId}/chapters.json  (snake_case JSON)
    |
    v  fetch via /api/r2/artifact/{jobId}/chapters.json
    |
Frontend response.json()  (still snake_case keys)
    |
    v  setChapters(chaptersData.chapters)  <-- NO transformation
    |
React state: Chapter[]  (TypeScript expects camelCase, but values are snake_case)
    |
    v  chapter.startSeconds  --> undefined
    |
    v  isFinite(undefined) = false
    |
Skip buttons silently fail
```

### Backend Self-Inconsistency

The backend's own `parse_chapters_manifest()` function in `chapters.py:149-197` expects **camelCase** keys:

```python
chapter_data.get("songTitle")     # camelCase
chapter_data.get("startSeconds")  # camelCase
chapter_data.get("endSeconds")    # camelCase
```

This means `generate_chapters_manifest()` → `asdict()` → `json.dumps()` → `parse_chapters_manifest()` would **fail** because the serializer produces snake_case but the parser expects camelCase.

### Frontend Type Definition

**File:** `webapp/src/lib/render/chapters.ts:40-46`

```typescript
export interface Chapter {
  position: number;
  songTitle: string;      // camelCase
  startSeconds: number;   // camelCase
  endSeconds: number;     // camelCase
  lines: ChapterLine[];
}
```

### Frontend Loading Code (No Transformation)

**File:** `webapp/src/app/songsets/[id]/play/controller/page.tsx:95-103`

```typescript
if (jobData.chaptersR2Key) {
  const chaptersProxyUrl = `/api/r2/artifact/${jobData.id}/chapters.json`;
  const chaptersDataResponse = await fetch(chaptersProxyUrl);
  if (chaptersDataResponse.ok) {
    const chaptersData = await chaptersDataResponse.json();
    if (chaptersData.chapters) {
      setChapters(chaptersData.chapters);  // <-- Raw assignment, no key mapping!
    }
  }
}
```

---

## Changes from v1 Plan

| Concern | v1 Approach | v2 Approach | Rationale |
|---|---|---|---|
| Serialization safety | Manual `to_camel_case_dict()` per class | Generic `dataclass_to_camel_case_dict()` helper | New fields are never silently dropped; auto-converts all `dataclasses.fields()` |
| Uploader test break | Not mentioned | Update `test_uploader.py` assertions | `song_title` → `songTitle` after fix; test would fail |
| Type safety | `RenderArtifacts.chapters: Any` | `ChaptersManifest \| None` | Calling `.to_camel_case_dict()` on `Any` is a runtime crash risk |
| Redundant fallbacks | `raw.chapters ?? raw["chapters"]` | `raw.chapters` only | Dot and bracket notation access the same property; `??` is a no-op |
| Duplicate Chapter type | Ambiguous (keep both or re-export) | Single source in `chapters.ts`, re-export from `LyricJumpList.tsx` | Prevents type drift |
| R2 data migration | Not addressed | Document normalization as permanent compatibility layer | No backfill; normalization stays forever for existing R2 data |

---

## Implementation Plan

### Fix Strategy

Two-pronged approach:
1. **Backend Fix**: Replace `asdict()` with a generic camelCase serializer so future renders produce correct JSON keys.
2. **Frontend Fix**: Add a permanent normalization function that converts snake_case keys to camelCase when loading chapters. This handles **existing** chapters.json files already stored in R2 with snake_case keys.

Both fixes are necessary because:
- Backend fix alone won't fix existing R2 data
- Frontend fix alone leaves the backend self-inconsistent

---

### Step 1: Add generic camelCase serialization helper to backend

**File:** `services/render-worker/src/sow_render_worker/chapters.py`

Add a generic `dataclass_to_camel_case_dict()` function that introspects `dataclasses.fields()` and auto-converts snake_case field names to camelCase. Then add `to_camel_case_dict()` methods to each dataclass that delegate to the helper, with `Chapter.to_camel_case_dict()` handling recursive `lines` conversion.

```python
import re

def _snake_to_camel(name: str) -> str:
    """Convert snake_case to camelCase. e.g. song_title -> songTitle"""
    components = name.split("_")
    return components[0] + "".join(x.title() for x in components[1:])

def dataclass_to_camel_case_dict(obj: object) -> dict | list | str | int | float | bool | None:
    """Recursively convert a dataclass instance to a dict with camelCase keys.

    Handles nested dataclasses, tuples (converted to lists), and primitive values.
    """
    if is_dataclass(obj) and not isinstance(obj, type):
        result = {}
        for f in fields(obj):
            value = getattr(obj, f.name)
            result[_snake_to_camel(f.name)] = dataclass_to_camel_case_dict(value)
        return result
    elif isinstance(obj, tuple):
        return [dataclass_to_camel_case_dict(item) for item in obj]
    elif isinstance(obj, list):
        return [dataclass_to_camel_case_dict(item) for item in obj]
    else:
        return obj
```

Then add `to_camel_case_dict()` methods that delegate to the helper:

```python
@dataclass(frozen=True)
class ChapterLine:
    text: str
    start_seconds: float

    def to_camel_case_dict(self) -> dict:
        return dataclass_to_camel_case_dict(self)


@dataclass(frozen=True)
class Chapter:
    position: int
    song_title: str
    start_seconds: float
    end_seconds: float
    lines: tuple[ChapterLine, ...] = field(default_factory=tuple)

    def to_camel_case_dict(self) -> dict:
        return dataclass_to_camel_case_dict(self)


@dataclass(frozen=True)
class ChaptersManifest:
    chapters: tuple[Chapter, ...] = field(default_factory=tuple)
    total_duration_seconds: float = 0.0
    generated_at: str = ""

    def to_camel_case_dict(self) -> dict:
        return dataclass_to_camel_case_dict(self)
```

**Why generic helper over manual methods:** If a developer adds a new field (e.g., `key_signature: str`) to any dataclass, it automatically appears in the serialized output. No risk of silent field omission.

---

### Step 2: Update uploader to use camelCase serializer + tighten type

**File:** `services/render-worker/src/sow_render_worker/uploader.py`

Replace `asdict()` with `to_camel_case_dict()`, remove the `asdict` import, and type `chapters` as `ChaptersManifest | None`:

```python
# Line 5, remove asdict from import:
from dataclasses import dataclass, field  # remove asdict

# Line 36, tighten type:
from sow_render_worker.chapters import ChaptersManifest

@dataclass
class RenderArtifacts:
    mp3_path: str | None = None
    mp4_path: str | None = None
    chapters: ChaptersManifest | None = None

# Lines 134-136, replace:
json_content = json.dumps(
    asdict(artifacts.chapters), indent=2, ensure_ascii=False
)

# With:
json_content = json.dumps(
    artifacts.chapters.to_camel_case_dict(), indent=2, ensure_ascii=False
)
```

---

### Step 3: Add backend roundtrip test

**File:** `services/render-worker/tests/test_chapters.py`

Add a test that proves `generate → serialize → parse` works end-to-end, plus a field-completeness test:

```python
class TestChaptersManifestRoundtrip:
    def test_serialize_and_parse_roundtrip(self):
        """Test that to_camel_case_dict() produces JSON that parse_chapters_manifest() can read."""
        chapters = [
            Chapter(
                position=1,
                song_title="Song A",
                start_seconds=0.0,
                end_seconds=30.0,
                lines=(ChapterLine(text="Line 1", start_seconds=5.0),),
            ),
            Chapter(
                position=2,
                song_title="Song B",
                start_seconds=30.0,
                end_seconds=60.0,
                lines=(),
            ),
        ]
        original = ChaptersManifest(
            chapters=tuple(chapters),
            total_duration_seconds=60.0,
            generated_at="2024-01-01T00:00:00Z",
        )

        # Serialize to camelCase JSON
        json_str = json.dumps(original.to_camel_case_dict())

        # Parse back
        parsed = parse_chapters_manifest(json_str)

        # Verify structure matches
        assert len(parsed.chapters) == 2
        assert parsed.chapters[0].song_title == "Song A"
        assert parsed.chapters[0].start_seconds == 0.0
        assert parsed.chapters[0].end_seconds == 30.0
        assert len(parsed.chapters[0].lines) == 1
        assert parsed.chapters[0].lines[0].text == "Line 1"
        assert parsed.chapters[0].lines[0].start_seconds == 5.0
        assert parsed.chapters[1].song_title == "Song B"
        assert parsed.total_duration_seconds == 60.0
        assert parsed.generated_at == "2024-01-01T00:00:00Z"

    def test_to_camel_case_dict_produces_camel_case_keys(self):
        """Verify the serialized JSON uses camelCase keys."""
        chapter = Chapter(
            position=1,
            song_title="Test Song",
            start_seconds=0.0,
            end_seconds=30.0,
            lines=(ChapterLine(text="Hello", start_seconds=5.0),),
        )
        manifest = ChaptersManifest(
            chapters=(chapter,),
            total_duration_seconds=30.0,
            generated_at="2024-01-01T00:00:00Z",
        )

        d = manifest.to_camel_case_dict()

        # Top-level keys
        assert "chapters" in d
        assert "totalDurationSeconds" in d
        assert "generatedAt" in d
        assert "total_duration_seconds" not in d

        # Chapter keys
        chapter_dict = d["chapters"][0]
        assert "position" in chapter_dict
        assert "songTitle" in chapter_dict
        assert "startSeconds" in chapter_dict
        assert "endSeconds" in chapter_dict
        assert "lines" in chapter_dict
        assert "song_title" not in chapter_dict
        assert "start_seconds" not in chapter_dict

        # Line keys
        line_dict = chapter_dict["lines"][0]
        assert "text" in line_dict
        assert "startSeconds" in line_dict
        assert "start_seconds" not in line_dict

    def test_to_camel_case_dict_covers_all_fields(self):
        """Verify that to_camel_case_dict() includes every dataclass field.

        This catches the case where a new field is added to a dataclass
        but the serialization is not updated.
        """
        from dataclasses import fields as dc_fields

        for cls in (ChapterLine, Chapter, ChaptersManifest):
            instance = cls.__new__(cls)
            for f in dc_fields(cls):
                # Set dummy values so getattr works
                default = f.default if f.default is not dataclasses.MISSING else (
                    f.default_factory() if f.default_factory is not dataclasses.MISSING else None
                )
                object.__setattr__(instance, f.name, default)

            camel_dict = instance.to_camel_case_dict()
            for f in dc_fields(cls):
                expected_key = _snake_to_camel(f.name)
                assert expected_key in camel_dict, (
                    f"{cls.__name__}.to_camel_case_dict() is missing field "
                    f"'{f.name}' (expected key '{expected_key}')"
                )
```

---

### Step 4: Update uploader tests

**File:** `services/render-worker/tests/test_uploader.py`

Fix the snake_case assertion and rewrite the `FakeChapter` test to use `ChaptersManifest`:

```python
# test_chapters_json_is_utf8_encoded — fix assertion:
def test_chapters_json_is_utf8_encoded(self):
    uploader = _make_uploader()
    uploader._client.put_object.return_value = {"ETag": '"etag"'}

    artifacts = RenderArtifacts(chapters=ChaptersManifest(
        chapters=(Chapter(position=1, song_title="中文標題", start_seconds=0.0, end_seconds=60.0),),
        total_duration_seconds=60.0,
        generated_at="",
    ))
    uploader.upload_render_artifacts("job-cn", artifacts)

    call_kwargs = uploader._client.put_object.call_args[1]
    body = call_kwargs["Body"]
    parsed = json.loads(body)
    assert parsed["chapters"][0]["songTitle"] == "中文標題"  # camelCase, not snake_case

# test_chapters_dataclass_serialization — rewrite to use ChaptersManifest:
def test_chapters_dataclass_serialization(self):
    uploader = _make_uploader()
    uploader._client.put_object.return_value = {"ETag": '"etag"'}

    artifacts = RenderArtifacts(chapters=ChaptersManifest(
        chapters=(Chapter(position=1, song_title="Test", start_seconds=0.0, end_seconds=60.0),),
        total_duration_seconds=60.0,
        generated_at="2024-01-01",
    ))
    uploader.upload_render_artifacts("job-dc", artifacts)

    call_kwargs = uploader._client.put_object.call_args[1]
    body = call_kwargs["Body"]
    parsed = json.loads(body)
    assert parsed["chapters"][0]["songTitle"] == "Test"
    assert parsed["chapters"][0]["startSeconds"] == 0.0
```

---

### Step 5: Add frontend normalization utility

**File:** `webapp/src/lib/render/chapters.ts`

Add a `normalizeChaptersManifest()` function that converts snake_case keys to camelCase. This is a **permanent compatibility layer** for existing chapters.json files in R2 that were serialized with `dataclasses.asdict()`.

```typescript
/**
 * Normalizes a chapters manifest from R2, converting snake_case keys to camelCase.
 *
 * PERMANENT COMPATIBILITY LAYER: Existing chapters.json files in R2 were
 * serialized with dataclasses.asdict() which preserves Python snake_case field
 * names. This function handles both formats so that old and new data work
 * correctly. Do not remove — existing R2 data will not be re-uploaded.
 *
 * @param data - Raw parsed JSON from chapters.json
 * @returns Normalized ChaptersManifest with camelCase keys
 */
export function normalizeChaptersManifest(data: unknown): ChaptersManifest {
  if (!data || typeof data !== "object") {
    throw new Error("Invalid chapters manifest: expected an object");
  }

  const raw = data as Record<string, unknown>;

  const chapters = raw.chapters;
  const totalDurationSeconds =
    (raw.totalDurationSeconds as number) ?? (raw.total_duration_seconds as number) ?? 0;
  const generatedAt =
    (raw.generatedAt as string) ?? (raw.generated_at as string) ?? "";

  if (!Array.isArray(chapters)) {
    throw new Error("Invalid chapters manifest: chapters must be an array");
  }

  const normalizedChapters: Chapter[] = chapters.map((chapter, index) => {
    if (!chapter || typeof chapter !== "object") {
      throw new Error(`Invalid chapter at index ${index}`);
    }

    const c = chapter as Record<string, unknown>;

    const position = (c.position as number) ?? index + 1;
    const songTitle =
      (c.songTitle as string) ?? (c.song_title as string) ?? `Song ${index + 1}`;
    const startSeconds =
      (c.startSeconds as number) ?? (c.start_seconds as number);
    const endSeconds =
      (c.endSeconds as number) ?? (c.end_seconds as number);
    const lines = (c.lines as unknown[]) ?? [];

    if (typeof startSeconds !== "number" || typeof endSeconds !== "number") {
      throw new Error(
        `Invalid chapter at index ${index}: missing or invalid startSeconds/endSeconds`
      );
    }

    if (!Array.isArray(lines)) {
      throw new Error(`Invalid chapter at index ${index}: lines must be an array`);
    }

    const normalizedLines: ChapterLine[] = lines.map((line, lineIndex) => {
      if (!line || typeof line !== "object") {
        throw new Error(`Invalid line at index ${lineIndex} in chapter ${index}`);
      }

      const l = line as Record<string, unknown>;

      const text = l.text as string;
      const lineStartSeconds =
        (l.startSeconds as number) ?? (l.start_seconds as number);

      if (typeof text !== "string" || typeof lineStartSeconds !== "number") {
        throw new Error(
          `Invalid line at index ${lineIndex} in chapter ${index}: missing text or startSeconds`
        );
      }

      return {
        text,
        startSeconds: lineStartSeconds,
      };
    });

    return {
      position: typeof position === "number" ? position : index + 1,
      songTitle: typeof songTitle === "string" ? songTitle : `Song ${index + 1}`,
      startSeconds,
      endSeconds,
      lines: normalizedLines,
    };
  });

  return {
    chapters: normalizedChapters,
    totalDurationSeconds:
      typeof totalDurationSeconds === "number" ? totalDurationSeconds : 0,
    generatedAt: typeof generatedAt === "string" ? generatedAt : "",
  };
}
```

---

### Step 6: Consolidate Chapter type — single source in chapters.ts

**File:** `webapp/src/components/play/LyricJumpList.tsx`

Replace the inline `Chapter` interface with a re-export from `chapters.ts`:

```typescript
// Remove the inline Chapter interface (lines 7-16)
// Add re-export:
export type { Chapter, ChapterLine } from "@/lib/render/chapters";
```

**File:** `webapp/src/app/songsets/[id]/play/controller/page.tsx`

Update the import to use the canonical source:

```typescript
// Change:
import { Chapter } from "@/components/play/LyricJumpList";

// To:
import type { Chapter } from "@/lib/render/chapters";
```

**File:** `webapp/src/components/play/ControllerPlayer.tsx`

Update the import (currently imports `Chapter` from `LyricJumpList`):

```typescript
// Change:
import { LyricJumpList, Chapter } from "./LyricJumpList";

// To:
import { LyricJumpList } from "./LyricJumpList";
import type { Chapter } from "@/lib/render/chapters";
```

---

### Step 7: Update frontend chapter loading to use normalization

**File:** `webapp/src/app/songsets/[id]/play/controller/page.tsx`

Import the normalization function and use it when loading chapters:

```typescript
// Add import at top:
import { normalizeChaptersManifest } from "@/lib/render/chapters";

// Update the chapters loading code (lines 95-103):
if (jobData.chaptersR2Key) {
  const chaptersProxyUrl = `/api/r2/artifact/${jobData.id}/chapters.json`;
  const chaptersDataResponse = await fetch(chaptersProxyUrl);
  if (chaptersDataResponse.ok) {
    const chaptersData = await chaptersDataResponse.json();
    try {
      const manifest = normalizeChaptersManifest(chaptersData);
      setChapters(manifest.chapters);
    } catch (e) {
      console.error("Failed to parse chapters:", e);
      // Continue without chapters rather than crashing
    }
  }
}
```

---

### Step 8: Add frontend tests for normalization

**File:** `webapp/src/test/lib/render/chapters.test.ts`

Add tests for `normalizeChaptersManifest`:

```typescript
import { normalizeChaptersManifest } from "@/lib/render/chapters";

describe("normalizeChaptersManifest", () => {
  it("normalizes snake_case keys to camelCase", () => {
    const snakeCaseData = {
      chapters: [
        {
          position: 1,
          song_title: "Song A",
          start_seconds: 0,
          end_seconds: 30,
          lines: [
            { text: "Line 1", start_seconds: 5 },
          ],
        },
      ],
      total_duration_seconds: 30,
      generated_at: "2024-01-01T00:00:00Z",
    };

    const manifest = normalizeChaptersManifest(snakeCaseData);

    expect(manifest.chapters).toHaveLength(1);
    expect(manifest.chapters[0].songTitle).toBe("Song A");
    expect(manifest.chapters[0].startSeconds).toBe(0);
    expect(manifest.chapters[0].endSeconds).toBe(30);
    expect(manifest.chapters[0].lines[0].startSeconds).toBe(5);
    expect(manifest.totalDurationSeconds).toBe(30);
    expect(manifest.generatedAt).toBe("2024-01-01T00:00:00Z");
  });

  it("passes through already-camelCase data unchanged", () => {
    const camelCaseData = {
      chapters: [
        {
          position: 1,
          songTitle: "Song A",
          startSeconds: 0,
          endSeconds: 30,
          lines: [
            { text: "Line 1", startSeconds: 5 },
          ],
        },
      ],
      totalDurationSeconds: 30,
      generatedAt: "2024-01-01T00:00:00Z",
    };

    const manifest = normalizeChaptersManifest(camelCaseData);

    expect(manifest.chapters).toHaveLength(1);
    expect(manifest.chapters[0].songTitle).toBe("Song A");
    expect(manifest.chapters[0].startSeconds).toBe(0);
  });

  it("handles mixed snake_case and camelCase keys", () => {
    const mixedData = {
      chapters: [
        {
          position: 1,
          songTitle: "Song A",      // camelCase
          start_seconds: 0,          // snake_case
          endSeconds: 30,            // camelCase
          lines: [
            { text: "Line 1", start_seconds: 5 },  // snake_case
          ],
        },
      ],
      total_duration_seconds: 30,    // snake_case
      generatedAt: "2024-01-01",     // camelCase
    };

    const manifest = normalizeChaptersManifest(mixedData);

    expect(manifest.chapters[0].songTitle).toBe("Song A");
    expect(manifest.chapters[0].startSeconds).toBe(0);
    expect(manifest.chapters[0].lines[0].startSeconds).toBe(5);
    expect(manifest.totalDurationSeconds).toBe(30);
  });

  it("throws on invalid chapters structure", () => {
    expect(() => normalizeChaptersManifest({ chapters: "not an array" }))
      .toThrow("chapters must be an array");
  });

  it("throws on missing startSeconds in chapter", () => {
    expect(() => normalizeChaptersManifest({
      chapters: [{ position: 1, songTitle: "Test" }],
    })).toThrow("missing or invalid startSeconds/endSeconds");
  });

  it("handles empty chapters array", () => {
    const manifest = normalizeChaptersManifest({
      chapters: [],
      totalDurationSeconds: 0,
      generatedAt: "2024-01-01",
    });

    expect(manifest.chapters).toEqual([]);
  });
});
```

---

### Step 9: Clean up defensive guards in ControllerPlayer

**File:** `webapp/src/components/play/ControllerPlayer.tsx`

The `isFinite()` guards added as a temporary fix can be simplified. Keep only the essential guard in `handleSeek` as defensive programming, but remove redundant guards in `handlePrevSong`, `handleNextSong`, `handleJumpToChapter`, and `handleJumpToLine` since the normalization ensures valid data.

**After fix:**

```typescript
const handleSeek = useCallback(
  (time: number) => {
    const video = videoRef.current;
    if (!video) return;

    // Defensive guard against non-finite values
    if (!isFinite(time)) {
      console.warn("handleSeek called with non-finite time:", time);
      return;
    }

    const clampedTime = Math.max(0, Math.min(duration, time));
    video.currentTime = clampedTime;
    setCurrentTime(clampedTime);
  },
  [duration]
);

const handlePrevSong = useCallback(() => {
  if (currentSongIndex > 0) {
    const prevChapter = chapters[currentSongIndex - 1];
    if (prevChapter) {
      handleSeek(prevChapter.startSeconds);
    }
  }
}, [currentSongIndex, chapters, handleSeek]);

const handleNextSong = useCallback(() => {
  if (currentSongIndex < chapters.length - 1) {
    const nextChapter = chapters[currentSongIndex + 1];
    if (nextChapter) {
      handleSeek(nextChapter.startSeconds);
    }
  }
}, [currentSongIndex, chapters, handleSeek]);

const handleJumpToChapter = useCallback(
  (index: number) => {
    if (index >= 0 && index < chapters.length) {
      const chapter = chapters[index];
      if (chapter) {
        handleSeek(chapter.startSeconds);
      }
    }
  },
  [chapters, handleSeek]
);

const handleJumpToLine = useCallback(
  (chapterIndex: number, lineIndex: number) => {
    if (chapterIndex >= 0 && chapterIndex < chapters.length) {
      const chapter = chapters[chapterIndex];
      if (chapter && lineIndex >= 0 && lineIndex < chapter.lines.length) {
        const line = chapter.lines[lineIndex];
        if (line) {
          handleSeek(line.startSeconds);
        }
      }
    }
  },
  [chapters, handleSeek]
);
```

---

## Summary of Files Changed

| File | Changes |
|------|---------|
| `services/render-worker/src/sow_render_worker/chapters.py` | Add `_snake_to_camel()`, `dataclass_to_camel_case_dict()`, and `to_camel_case_dict()` methods on `ChapterLine`, `Chapter`, `ChaptersManifest` |
| `services/render-worker/src/sow_render_worker/uploader.py` | Replace `asdict()` with `to_camel_case_dict()`, remove `asdict` import, type `chapters` as `ChaptersManifest \| None` |
| `services/render-worker/tests/test_chapters.py` | Add `TestChaptersManifestRoundtrip` class with roundtrip, key-format, and field-completeness tests |
| `services/render-worker/tests/test_uploader.py` | Fix `song_title` → `songTitle` assertion; rewrite `FakeChapter` test to use `ChaptersManifest` |
| `webapp/src/lib/render/chapters.ts` | Add `normalizeChaptersManifest()` function (permanent compatibility layer) |
| `webapp/src/components/play/LyricJumpList.tsx` | Replace inline `Chapter` interface with re-export from `@/lib/render/chapters` |
| `webapp/src/app/songsets/[id]/play/controller/page.tsx` | Use `normalizeChaptersManifest()` when loading chapters; update `Chapter` import |
| `webapp/src/components/play/ControllerPlayer.tsx` | Simplify `isFinite` guards (keep only in `handleSeek`); update `Chapter` import |
| `webapp/src/test/lib/render/chapters.test.ts` | Add tests for `normalizeChaptersManifest` |

---

## Implementation Order

1. **Backend: Add generic helper + `to_camel_case_dict()` methods** (`chapters.py`)
2. **Backend: Update uploader** (`uploader.py`) — replace `asdict`, tighten type
3. **Backend: Add roundtrip + field-completeness tests** (`test_chapters.py`)
4. **Backend: Update uploader tests** (`test_uploader.py`)
5. **Backend: Run tests** to verify serialization works
6. **Frontend: Add `normalizeChaptersManifest()`** (`chapters.ts`)
7. **Frontend: Consolidate Chapter type** — re-export from `chapters.ts` in `LyricJumpList.tsx`
8. **Frontend: Add normalization tests** (`chapters.test.ts`)
9. **Frontend: Run tests** to verify normalization works
10. **Frontend: Update controller page** to use normalization
11. **Frontend: Clean up defensive guards** in ControllerPlayer
12. **Frontend: Run tests** to verify everything works
13. **Integration test**: Re-render a songset and verify chapter skip buttons work

---

## Verification Checklist

- [ ] `services/render-worker/tests/test_chapters.py` passes with new roundtrip + field-completeness tests
- [ ] `services/render-worker/tests/test_uploader.py` passes with updated assertions
- [ ] `webapp/src/test/lib/render/chapters.test.ts` passes with normalization tests
- [ ] `webapp/src/test/components/play/ControllerPlayer.test.tsx` passes
- [ ] `webapp/pnpm lint` passes
- [ ] Manual test: Load a songset with existing chapters.json (snake_case) — skip buttons work
- [ ] Manual test: Re-render a songset — new chapters.json has camelCase keys
- [ ] Manual test: Load newly rendered songset — skip buttons work

---

## Expected Impact

| Metric | Before | After |
|--------|--------|-------|
| Chapter skip buttons | Do nothing | Jump to correct chapter |
| `chapter.startSeconds` | `undefined` | Valid number |
| `isFinite(chapter.startSeconds)` | `false` | `true` |
| Backend self-consistency | Broken (`asdict` produces snake_case, parser expects camelCase) | Fixed (both use camelCase) |
| Existing R2 chapters.json files | Broken | Fixed by frontend normalization (permanent) |
| Future chapters.json files | Would be broken | Correct camelCase from the start |
| New dataclass fields | Risk of silent omission from JSON | Auto-included by generic helper |
