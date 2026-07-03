# Enhance Browse Sheet Desktop Layout

## Goal

Improve the Browse Song sheet layout on desktop to reduce wasted horizontal space and increase the number of visible song rows.

**Current problems (desktop only):**
1. **Horizontal empty space** — Song cards stretch full-width in a single column, but their compact content (album art + text + add button) leaves a lot of unused whitespace on the right.
2. **Few rows visible** — The sheet is only `75vh` on desktop (`sm:`), with a tall header, mode tabs, search controls, and a large bottom padding (`pb-28`) eating vertical space. Song results end up confined to the bottom half of the sheet.

Mobile (Pixel 6 Chrome) is already fine; keep the current mobile experience untouched.

## User Decisions

| Decision | Choice |
|---|---|
| Max columns | **2** (`sm:grid-cols-2`) — wider cards on desktop, avoids excessive truncation |
| Mobile layout | **Unchanged** — single column (`grid-cols-1`) still fine on phones |
| Sheet height on desktop | **90vh** (`sm:90vh`) — currently 75vh, which is inverted vs. mobile (85vh) |
| Search controls | **Slightly compressed** — reduce vertical spacing so results start higher |

## Files to Modify

### 1. `webapp/src/components/songset/BrowseSheet.tsx`

#### 1a. Increase sheet height on desktop

Change the `SheetContent` height class from mobile-first 85vh / desktop 75vh to mobile 85vh / desktop 90vh.

```diff
- className={cn("data-[side=bottom]:!h-[85vh] sm:data-[side=bottom]:!h-[75vh] overflow-hidden", className)}
+ className={cn("data-[side=bottom]:!h-[85vh] sm:data-[side=bottom]:!h-[90vh] overflow-hidden", className)}
```

#### 1b. Compress header

Remove the `<SheetDescription>` line from the header to reclaim vertical space. Keep the title.

```diff
         <SheetHeader className="pb-4">
           <SheetTitle>Search Songs</SheetTitle>
-          <SheetDescription>
-            Search and add songs to your songset
-          </SheetDescription>
         </SheetHeader>
```

Update `SheetHeader` padding to be tighter:

```diff
- <SheetHeader className="pb-4">
+ <SheetHeader className="pb-2">
```

#### 1c. Reduce bottom padding on desktop

The audio player bar is roughly ~100px tall on desktop. `pb-28` (112px) is safe but wasteful. Reduce to `pb-20` (80px) on desktop while keeping `pb-28` on mobile (which looks fine today).

```diff
- <div className={cn("flex flex-col h-full min-h-0", currentTrack ? "pb-28" : "pb-8")}>
+ <div className={cn("flex flex-col h-full min-h-0", currentTrack ? "pb-28 sm:pb-20" : "pb-8")}>
```

#### 1d. Two-column grid for song results

Switch the results list from a single-column stack to a responsive grid.

```diff
                 {!error && results.length > 0 && (
-                  <div className="space-y-2 pb-4">
+                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 pb-4">
                     {results.map((song) => (
```

### 2. `webapp/src/components/songset/SongSearch.tsx`

#### 2a. Reduce wrapper vertical spacing

```diff
-   <div className={cn("space-y-3", className)} data-testid="song-search">
+   <div className={cn("space-y-2", className)} data-testid="song-search">
```

#### 2b. Compress advanced filters panel

```diff
-           className="border rounded-md p-3 space-y-4"
+           className="border rounded-md p-2.5 space-y-3"
```

### 3. `webapp/src/components/songset/SongCard.tsx`

#### 3a. Tighten card padding

Slightly reduce card density so more rows fit vertically, without hurting readability.

```diff
-     <CardContent className="p-3">
+     <CardContent className="p-2.5">
```

## Testing Plan

1. **Manual visual check** (desktop Chrome, 1440px+ width):
   - Open Browse Song sheet in Songset Editor.
   - Verify 2 columns of `SongCard` with no horizontal overflow.
   - Verify no overlap between results and the fixed audio player bar when a preview is playing.
   - Verify sheet height feels comfortable (~90vh) and the search controls + results are well-balanced.

2. **Manual visual check** (mobile Chrome / Pixel 6):
   - Open Browse Song sheet.
   - Confirm single-column layout is unchanged.
   - Confirm 85vh height is unchanged.
   - Confirm header description removal does not feel confusing.

3. **Automated tests**:
   - `BrowseSheet.test.tsx` — existing tests for rendering song cards, search, add, done button, tabs should still pass. The `space-y-2` → grid change is cosmetic; test IDs remain the same.
   - `SongCard.test.tsx` — padding change is cosmetic; test IDs and assertions remain valid.
   - `accessibility.test.tsx` — `BrowseSheet` tests only check `role="tablist"` and `aria-selected`; removing `SheetDescription` does not affect these.
   - Run: `cd delivery/webapp && pnpm test`

## Rollback

All changes are CSS class string adjustments. Revert the 3 files to restore the previous layout instantly.
