# Fix: Chapter Skip Buttons Not Working — snake_case vs camelCase Mismatch

> **Status:** Implementation plan ready.
> **Discovered:** 2026-05-26

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

**File:** `webapp/src/components/play/LyricJumpList.tsx:7-16`

```typescript
export interface Chapter {
  position: number;
  songTitle: string;      // camelCase
  startSeconds: number;   // camelCase
  endSeconds: number;     // camelCase
  lines: {
    text: string;
    startSeconds: number; // camelCase
  }[];
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

## Implementation Plan

### Fix Strategy

Two-pronged approach:

1. **Backend Fix**: Replace `asdict()` with a camelCase-aware serializer so future renders produce correct JSON keys.
2. **Frontend Fix**: Add a normalization function that converts snake_case keys to camelCase when loading chapters. This handles **existing** chapters.json files already stored in R2 with snake_case keys.

Both fixes are necessary because:
- Backend fix alone won't fix existing R2 data
- Frontend fix alone leaves the backend self-inconsistent

---

### Step 1: Add camelCase serialization to backend

**File:** `services/render-worker/src/sow_render_worker/chapters.py`

Add a `to_camel_case_dict()` method to each dataclass that produces camelCase keys:

```python
@dataclass(frozen=True)
class ChapterLine:
    text: str
    start_seconds: float

    def to_camel_case_dict(self) -> dict:
        return {
            "text": self.text,
            "startSeconds": self.start_seconds,
        }


@dataclass(frozen=True)
class Chapter:
    position: int
    song_title: str
    start_seconds: float
    end_seconds: float
    lines: tuple[ChapterLine, ...] = field(default_factory=tuple)

    def to_camel_case_dict(self) -> dict:
        return {
            "position": self.position,
            "songTitle": self.song_title,
            "startSeconds": self.start_seconds,
            "endSeconds": self.end_seconds,
            "lines": [line.to_camel_case_dict() for line in self.lines],
        }


@dataclass(frozen=True)
class ChaptersManifest:
    chapters: tuple[Chapter, ...] = field(default_factory=tuple)
    total_duration_seconds: float = 0.0
    generated_at: str = ""

    def to_camel_case_dict(self) -> dict:
        return {
            "chapters": [chapter.to_camel_case_dict() for chapter in self.chapters],
            "totalDurationSeconds": self.total_duration_seconds,
            "generatedAt": self.generated_at,
        }
```

---

### Step 2: Update uploader to use camelCase serializer

**File:** `services/render-worker/src/sow_render_worker/uploader.py`

Replace `asdict()` with `to_camel_case_dict()`:

```python
# Line 134-136, replace:
json_content = json.dumps(
    asdict(artifacts.chapters), indent=2, ensure_ascii=False
)

# With:
json_content = json.dumps(
    artifacts.chapters.to_camel_case_dict(), indent=2, ensure_ascii=False
)
```

Also remove the `asdict` import since it's no longer needed:

```python
# Line 5, remove asdict from import:
from dataclasses import dataclass, field  # remove asdict
```

---

### Step 3: Add backend roundtrip test

**File:** `services/render-worker/tests/test_chapters.py`

Add a test that proves `generate → serialize → parse` works end-to-end:

```python
class TestChaptersManifestRoundtrip:
    def test_serialize_and_parse_roundtrip(self):
        """Test that to_camel_case_dict() produces JSON that parse_chapters_manifest() can read."""
        # Create a manifest with chapters
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
```

---

### Step 4: Add frontend normalization utility

**File:** `webapp/src/lib/render/chapters.ts`

Add a `normalizeChaptersManifest()` function that converts snake_case keys to camelCase:

```typescript
/**
 * Normalizes a chapters manifest from R2, converting snake_case keys to camelCase.
 * 
 * This handles existing chapters.json files that were serialized with
 * dataclasses.asdict() (which preserves Python snake_case field names).
 * 
 * @param data - Raw parsed JSON from chapters.json
 * @returns Normalized ChaptersManifest with camelCase keys
 */
export function normalizeChaptersManifest(data: unknown): ChaptersManifest {
  if (!data || typeof data !== "object") {
    throw new Error("Invalid chapters manifest: expected an object");
  }

  const raw = data as Record<string, unknown>;

  // Handle both camelCase and snake_case for top-level keys
  const chapters = raw.chapters ?? raw["chapters"];
  const totalDurationSeconds = raw.totalDurationSeconds ?? raw["total_duration_seconds"] ?? 0;
  const generatedAt = raw.generatedAt ?? raw["generated_at"] ?? "";

  if (!Array.isArray(chapters)) {
    throw new Error("Invalid chapters manifest: chapters must be an array");
  }

  const normalizedChapters: Chapter[] = chapters.map((chapter, index) => {
    if (!chapter || typeof chapter !== "object") {
      throw new Error(`Invalid chapter at index ${index}`);
    }

    const c = chapter as Record<string, unknown>;

    // Handle both camelCase and snake_case for chapter keys
    const position = c.position ?? c["position"] ?? index + 1;
    const songTitle = c.songTitle ?? c["song_title"] ?? `Song ${index + 1}`;
    const startSeconds = c.startSeconds ?? c["start_seconds"];
    const endSeconds = c.endSeconds ?? c["end_seconds"];
    const lines = c.lines ?? c["lines"] ?? [];

    if (typeof startSeconds !== "number" || typeof endSeconds !== "number") {
      throw new Error(`Invalid chapter at index ${index}: missing or invalid startSeconds/endSeconds`);
    }

    if (!Array.isArray(lines)) {
      throw new Error(`Invalid chapter at index ${index}: lines must be an array`);
    }

    const normalizedLines: ChapterLine[] = lines.map((line, lineIndex) => {
      if (!line || typeof line !== "object") {
        throw new Error(`Invalid line at index ${lineIndex} in chapter ${index}`);
      }

      const l = line as Record<string, unknown>;

      const text = l.text ?? l["text"];
      const lineStartSeconds = l.startSeconds ?? l["start_seconds"];

      if (typeof text !== "string" || typeof lineStartSeconds !== "number") {
        throw new Error(`Invalid line at index ${lineIndex} in chapter ${index}: missing text or startSeconds`);
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
    totalDurationSeconds: typeof totalDurationSeconds === "number" ? totalDurationSeconds : 0,
    generatedAt: typeof generatedAt === "string" ? generatedAt : "",
  };
}
```

---

### Step 5: Update frontend chapter loading to use normalization

**File:** `webapp/src/app/songsets/[id]/play/controller/page.tsx`

Import the normalization function and use it when loading chapters:

```typescript
// Add import at top:
import { normalizeChaptersManifest, Chapter } from "@/lib/render/chapters";

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

Also update the import for Chapter type — it should come from `@/lib/render/chapters` instead of `@/components/play/LyricJumpList` to avoid duplication:

```typescript
// Change:
import { Chapter } from "@/components/play/LyricJumpList";

// To:
import type { Chapter } from "@/lib/render/chapters";
```

Then update `LyricJumpList.tsx` to re-export the type:

```typescript
// In LyricJumpList.tsx, change the interface to a re-export:
export type { Chapter } from "@/lib/render/chapters";
```

Or keep the interface in LyricJumpList.tsx and have both locations define the same type (current approach). The key is that `normalizeChaptersManifest` returns the same shape.

---

### Step 6: Add frontend tests for normalization

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

### Step 7: Clean up defensive guards in ControllerPlayer

**File:** `webapp/src/components/play/ControllerPlayer.tsx`

The `isFinite()` guards added as a temporary fix can be simplified. Keep only the essential guard in `handleSeek` as defensive programming, but remove redundant guards in `handlePrevSong`, `handleNextSong`, `handleJumpToChapter`, and `handleJumpToLine` since the normalization ensures valid data.

**Current (after temporary fix):**

```typescript
const handleSeek = useCallback(
  (time: number) => {
    const video = videoRef.current;
    if (!video) return;

    if (!isFinite(time)) return;  // Keep this as defensive guard

    const clampedTime = Math.max(0, Math.min(duration, time));
    video.currentTime = clampedTime;
    setCurrentTime(clampedTime);
  },
  [duration]
);

const handlePrevSong = useCallback(() => {
  if (currentSongIndex > 0) {
    const prevChapter = chapters[currentSongIndex - 1];
    if (prevChapter && isFinite(prevChapter.startSeconds)) {  // Remove this check
      handleSeek(prevChapter.startSeconds);
    }
  }
}, [currentSongIndex, chapters, handleSeek]);
```

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
| `services/render-worker/src/sow_render_worker/chapters.py` | Add `to_camel_case_dict()` methods to `ChapterLine`, `Chapter`, `ChaptersManifest` |
| `services/render-worker/src/sow_render_worker/uploader.py` | Replace `asdict()` with `to_camel_case_dict()`, remove unused import |
| `services/render-worker/tests/test_chapters.py` | Add `TestChaptersManifestRoundtrip` class with roundtrip and key-format tests |
| `webapp/src/lib/render/chapters.ts` | Add `normalizeChaptersManifest()` function |
| `webapp/src/app/songsets/[id]/play/controller/page.tsx` | Use `normalizeChaptersManifest()` when loading chapters |
| `webapp/src/test/lib/render/chapters.test.ts` | Add tests for `normalizeChaptersManifest` |
| `webapp/src/components/play/ControllerPlayer.tsx` | Simplify `isFinite` guards (keep only in `handleSeek`) |

---

## Implementation Order

1. **Backend: Add `to_camel_case_dict()` methods** (`chapters.py`)
2. **Backend: Update uploader** (`uploader.py`)
3. **Backend: Add roundtrip tests** (`test_chapters.py`)
4. **Backend: Run tests** to verify serialization works
5. **Frontend: Add `normalizeChaptersManifest()`** (`chapters.ts`)
6. **Frontend: Add normalization tests** (`chapters.test.ts`)
7. **Frontend: Run tests** to verify normalization works
8. **Frontend: Update controller page** to use normalization
9. **Frontend: Clean up defensive guards** in ControllerPlayer
10. **Frontend: Run tests** to verify everything works
11. **Integration test**: Re-render a songset and verify chapter skip buttons work

---

## Verification Checklist

- [ ] `services/render-worker/tests/test_chapters.py` passes with new roundtrip tests
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
| Existing R2 chapters.json files | Broken | Fixed by frontend normalization |
| Future chapters.json files | Would be broken | Correct camelCase from the start |
