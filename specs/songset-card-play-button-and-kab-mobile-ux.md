# Songset Card: Prominent Play Button + KAB Menu Mobile UX Fix

## Problem

Two UX issues on the Songsets List screen (`/songsets`):

1. **Play action is buried in KAB menu**: After a successful render, users must discover the Play action inside the kebab (three-dot) dropdown menu. Since worship playback is the primary use case, Play should be immediately visible on the card.

2. **KAB menu invisible on mobile**: The kebab menu button uses `opacity-0 group-hover:opacity-100` (SongsetRow.tsx:133), making it invisible on mobile browsers where hover doesn't exist. Users have no visual indication that a menu exists.

## Solution

### 1. Add Prominent Play Button to Songset Card

**Visibility condition**: Show when `renderState === "fresh" || renderState === "stale"` (i.e., a completed render exists, even if outdated). The user can still play a stale render's output.

**Style**: Primary variant (`variant="default"`), small size (`size="sm"`), with Play icon + "Play" text.

**Placement**: In the header row, between the songset name link and the KAB menu button. Natural reading flow: name → play → more options.

**Remove from KAB menu**: Delete the "Play" menu item from the dropdown since it's now redundant. This also shortens the menu, improving scanability.

### 2. Fix KAB Menu Mobile Discoverability

**Approach**: Use responsive breakpoint to make the kebab button always visible on mobile, keep hover-reveal on desktop.

Change from:
```
opacity-0 group-hover:opacity-100 focus:opacity-100
```

To:
```
opacity-100 lg:opacity-0 lg:group-hover:opacity-100 focus:opacity-100
```

- **Mobile (< lg breakpoint)**: Button always visible (`opacity-100`)
- **Desktop (>= lg breakpoint)**: Hidden by default, revealed on card hover (`lg:opacity-0` + `lg:group-hover:opacity-100`)
- **Both**: Visible when focused (`focus:opacity-100`)

## Files to Modify

### `webapp/src/components/songset/SongsetRow.tsx`

#### Change 1: Add Play button in header row

Insert between the `<Link>` (lines 113-125) and the `<DropdownMenu>` (lines 128-184):

```tsx
{(renderState === "fresh" || renderState === "stale") && onPlay && (
  <Button
    variant="default"
    size="sm"
    className="shrink-0 gap-1.5"
    onClick={(e) => {
      e.preventDefault();
      onPlay?.();
    }}
  >
    <Play className="size-4" />
    Play
  </Button>
)}
```

Key details:
- `e.preventDefault()` prevents click from bubbling to any parent link
- `shrink-0` prevents button from being squeezed in the flex row
- `variant="default"` gives primary/accent color for prominence
- `size="sm"` keeps proportional alongside the KAB icon button
- `Play` icon is already imported (line 24)

#### Change 2: Remove "Play" from KAB menu

Delete lines 153-156:

```tsx
<DropdownMenuItem onClick={onPlay}>
  <Play className="size-4 mr-2" />
  Play
</DropdownMenuItem>
```

#### Change 3: Fix KAB button mobile visibility

On line 133, change the Button's className from:

```
shrink-0 opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity
```

To:

```
shrink-0 opacity-100 lg:opacity-0 lg:group-hover:opacity-100 focus:opacity-100 transition-opacity
```

### `webapp/src/test/components/songset/SongsetRow.test.tsx`

- Add test: Play button is visible when `renderState === "fresh"`
- Add test: Play button is visible when `renderState === "stale"`
- Add test: Play button is not visible when `renderState === "unrendered"`
- Add test: Play button is not visible when `renderState === "rendering"`
- Add test: Play button is not visible when `renderState === "failed"`
- Add test: Clicking Play button calls `onPlay` callback
- Update test: Remove assertion for "Play" menu item in KAB dropdown

## No Other Files Need Changes

- `SongsetList.tsx` — already passes `onPlay` and `renderState` props through to `SongsetRow`
- `songsets/page.tsx` — already provides `handlePlay` callback
- `RenderStatusBadge.tsx` — no changes needed
- `dropdown-menu.tsx` — no changes needed

## Visual Layout (After Changes)

```
┌─────────────────────────────────────────────────┐
│  Songset Name               [▶ Play] [⋮ KAB]   │
│  Description                                     │
│  ♪ 5 songs  ⏱ 12:30  Updated Jun 6             │
│  [✓ Rendered]                                    │
└─────────────────────────────────────────────────┘
```

- On mobile: KAB button (⋮) is always visible
- On desktop: KAB button appears on card hover
- Play button appears only when render is fresh or stale
