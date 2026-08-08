# Fix: Webapp Player — Locate Songsets Popover Clipping (v3)

## Status

Spec written, **do not implement** until reviewed. Supersedes v2 (commit c433baa4).

## What v2 Fixed (And Why It Was Insufficient)

The v2 fix (commit c433baa4) addressed three real bugs:
1. ✅ CSS variable refs: `max-h-[var(--popover-available-height)]` → `max-h-(--available-height)` — max-height now resolves to Base UI's pixel value.
2. ✅ Removed inner `max-h-64 overflow-y-auto` on the list container.
3. ✅ Fixed ARIA roles + focus-on-mount guard.

These changes correctly enabled viewport-aware max-height and single-surface scrolling. The popup now caps at `--available-height` and scrolls internally.

**However, clipping persists for a different reason that v2 did not investigate or address.**

## New Root Cause: z-index Conflict + Automatic Placement Flip

### How Base UI positions the popover

The `PopoverContent` wrapper in `popover.tsx` does not explicitly set a `side` on the `Positioner`. Base UI defaults to `side="top"` for the preferred placement, but the `flip` middleware (enabled by default via `collisionAvoidance.side = 'flip'`) can relocate the popup when content overflows the available space.

### Observed behavior (via Chrome DevTools on https://localhost:8080/songsets/songset_20260804001152747555)

Measured with mock data (via fetch override) at various item counts:

| Item count | data-side | Popup rect              | Available height | Clipped? |
|------------|-----------|-------------------------|------------------|----------|
| 1          | top       | top=632, bottom=686, h=54  | 680px            | No       |
| 12         | top       | top=49, bottom=686, h=637 | 680px            | No       |
| 13         | right     | top=28, bottom=718, h=690 | 721px            | No*      |
| 14         | right     | top=6, bottom=727, h=721  | 721px            | **Yes**  |
| 15         | right     | top=6, bottom=727, h=721  | 721px            | **Yes**  |
| 20         | right     | top=6, bottom=727, h=721  | 721px            | **Yes**  |

\* At 13 items the popup height (690px) fits within the 721px max, but the bottom (718) still overlaps the player bar (top=647).

### Why items get hidden

When content exceeds the "top" available height (~680px), the `flip` middleware switches placement to `"right"`. With `side="right"`, the popup extends the full viewport height:

```
Popup rect:       top=6,    bottom=727,  height=721
Player bar rect:  top=647,  bottom=732,  height=85
Overlap:          727 - 647 = 80px
```

The positioner has `z-index: 50` (`popover.tsx:40`), but the player bar has `z-index: 60` (`AudioPlayerBar.tsx:160`). **The player bar renders on top of the popup**, covering the bottom 80px — approximately 1.5 list items.

With 20 items injected, item-by-item inspection confirmed:
- Items 0–11: fully visible
- Item 12: 49px of 53px covered by player bar
- Items 13–19: fully behind player bar

The popup has internal scrolling (scrollHeight=1059, clientHeight=719), but the user can only scroll by mouse wheel — the bottom of the scrollbar is also behind the player bar.

### Why v2's test didn't catch this

The v2 test uses 6 mock songsets (≈318px content). At 6 items, the popup easily fits within the "top" available height (680px). No placement flip occurs, and there is no overlap. The test asserts `className` changes but cannot verify visual stacking or placement behavior (jsdom does not apply CSS layout or z-index).

### Why v2 appeared effective but wasn't

The v2 test's assertion "all 6 songset names are rendered" would pass because jsdom renders all DOM nodes regardless of CSS clipping, and at 6 items there is no real clipping even in a browser. The fix would only fail when the song appears in 13+ songsets — a scenario that was never tested.

## Secondary Bug: `side` Prop Not Forwarded to Positioner

The `PopoverContent` wrapper in `popover.tsx` accepts `side` in its TypeScript type:

```tsx
function PopoverContent({
  className,
  align = "center",
  sideOffset = 4,
  ...props
}: PopoverPrimitive.Popup.Props &
  Pick<PopoverPrimitive.Positioner.Props, "align" | "sideOffset" | "side">) {
```

But `side` is not destructured — it falls into `...props`, which is spread onto `PopoverPrimitive.Popup` (line 48), not `PopoverPrimitive.Positioner` (line 37). The Popup component does not accept a `side` prop; it is silently ignored. This means callers **cannot control** the placement direction through `PopoverContent`, even though the type signature suggests they can.

## Proposed Fix

Two files, four changes.

### Change 1: `popover.tsx` — Fix `side` and `collisionAvoidance` prop forwarding

Destructure `side` and `collisionAvoidance` from the function params and pass them to `<PopoverPrimitive.Positioner>`:

```tsx
function PopoverContent({
  className,
  align = "center",
  side,
  sideOffset = 4,
  collisionAvoidance,
  ...props
}: PopoverPrimitive.Popup.Props &
  Pick<
    PopoverPrimitive.Positioner.Props,
    "align" | "sideOffset" | "side" | "collisionAvoidance"
  >) {
  return (
    <PopoverPrimitive.Portal>
      <PopoverPrimitive.Positioner
        align={align}
        side={side}
        sideOffset={sideOffset}
        collisionAvoidance={collisionAvoidance}
        className="z-[70]"
      >
        <PopoverPrimitive.Popup
          data-slot="popover-content"
          className={cn(
            "bg-popover text-popover-foreground data-[starting-style]:data-[state=open]:animate-in data-[starting-style]:data-[state=open]:fade-in-0 data-[starting-style]:data-[state=open]:zoom-in-95 data-[ending-style]:data-[state=closed]:animate-out data-[ending-style]:data-[state=closed]:fade-out-0 data-[ending-style]:data-[state=closed]:zoom-out-95 relative max-h-(--available-height) origin-(--transform-origin) overflow-y-auto overflow-x-hidden rounded-md border p-4 shadow-md outline-none",
            className
          )}
          {...props}
        />
      </PopoverPrimitive.Positioner>
    </PopoverPrimitive.Portal>
  );
}
```

### Change 2: `popover.tsx` — Increase z-index above the player bar

Change the positioner's `className` from `"z-50"` to `"z-[70]"`. The player bar is `z-[60]`, so the popover must be higher to render above it.

This change is in the same file as Change 1 (combined in the code snippet above).

### Change 3: `LocateSongsetsPopover.tsx` — Force `side="top"` and disable flip

Pass `side="top"` and `collisionAvoidance={{ side: 'none', fallbackAxisSide: 'none' }}` to `PopoverContent`:

```tsx
<PopoverContent
  className="w-72 p-0"
  align="start"
  side="top"
  collisionAvoidance={{ side: 'none', fallbackAxisSide: 'none' }}
>
```

This forces the popup to always open **above** the trigger button (which is inside the bottom-fixed player bar), never to the right or below. The `collisionAvoidance.side = 'none'` disables the `flip` middleware so the popup never gets relocated to `"right"` when content is large. The `fallbackAxisSide = 'none'` prevents perpendicular-axis fallback.

With `side="top"` active, Base UI's `size` middleware sets `--available-height` to the distance from the top of the viewport to the trigger (≈680px in the test viewport). The existing `max-h-(--available-height)` CSS class caps the popup at this value, and `overflow-y: auto` provides internal scrolling for overflow.

The popup bottom is anchored at `trigger.top - sideOffset` (≈686px), well within the viewport. The popup never extends into the player bar area (top=647+).

### Why not just increase z-index?

Increasing z-index alone (without `side="top"`) would make the popup render **on top of** the player bar in the overlap zone (80px). The popup's bottom rows would visually cover the player bar controls — functional but ugly. Forcing `side="top"` visually separates the popup from the player bar. The z-index increase is kept as a safety measure for transition animations where the popup might be mid-flight.

### Change 4: No changes to `LocateSongsetsPopover.tsx` list container

The v2 removal of `max-h-64 overflow-y-auto` from the list container is retained. No further changes are needed there.

## Files to Modify

| File | Changes |
|------|---------|
| `delivery/webapp/src/components/ui/popover.tsx` | (1) Destructure `side` and `collisionAvoidance` and pass them to `<PopoverPrimitive.Positioner>`. (2) Change `z-50` → `z-[70]`. |
| `delivery/webapp/src/components/audio/LocateSongsetsPopover.tsx` | Pass `side="top"` and `collisionAvoidance={{ side: 'none', fallbackAxisSide: 'none' }}` to `<PopoverContent>`. |

## Blast Radius Analysis

`PopoverContent` has one consumer in the entire codebase: `LocateSongsetsPopover.tsx` (confirmed via grep in v2 spec). The z-index increase from `z-50` → `z-[70]` affects all popovers theoretically, but since there is only one popover in the app, the blast radius is zero.

The `side` and `collisionAvoidance` props default to `undefined` when not passed, so existing callers (there are none besides `LocateSongsetsPopover`) see no behavior change.

The `collisionAvoidance={{ side: 'none' }}` is set on `LocateSongsetsPopover` only, not on `popover.tsx` defaults, so it cannot affect other popovers.

## Testing Plan

### Manual / DevTools verification

1. **1 item (baseline):** Open the popover for a song in 1 songset.
   - Expect: `data-side="top"`, popup fully visible above player bar, no scrollbar.

2. **12 items (fits in "top" height):** Mock fetch to return 12 songsets.
   - Expect: `data-side="top"`, popup top ≈49px, bottom ≈686px, all items visible, no scrollbar.

3. **15 items (exceeds "top" height, would previously flip to "right"):** Mock fetch to return 15 songsets.
   - Expect: `data-side="top"` (NOT "right" — flip is disabled), popup capped at `--available-height` (~680px), `scrollHeight > clientHeight`, scrollbar visible. Popup bottom at ~686px, NOT extending into player bar area.

4. **20 items (stress test):** Mock fetch to return 20 songsets.
   - Expect: `data-side="top"`, popup at max height with scrollbar. Scroll to bottom — last item fully visible within the popup's scroll area, NOT hidden behind player bar.

5. **Z-index check:** Inspect positioner element.
   - Expect: `z-index` computed value is 70 (not 50). Player bar z-index is 60. Popover renders above player bar.

6. **Lyrics panel open + popover:** Open lyrics panel (`L` key), then open the popover.
   - Expect: `data-side="top"`, available height reduced (lyrics panel pushes trigger higher). Popup caps at smaller available height, scrolls internally. No overlap with player bar.

7. **Short viewport (mobile):** Resize to small height (e.g., 400px landscape).
   - Expect: `data-side="top"`, popup caps at whatever space is available above trigger. Scrollable, functional even if small.

### Automated tests

**jsdom limitation:** jsdom does not apply CSS layout, z-index, or Floating UI positioning. Tests focus on prop forwarding assertions.

1. **`AudioPlayerBar.test.tsx` — Update `LocateSongsetsPopover` tests:**

   a. **Increase mock data to 15 items** (up from 6) to represent the clipping scenario.

   b. **Assert `side="top"` prop on PopoverContent:** Since jsdom doesn't render Floating UI positioning, test that the `LocateSongsetsPopover` passes `side="top"` to `PopoverContent`. This can be done by spying on `PopoverContent` or by asserting the component's rendered output includes the expected props.

   c. **Assert `collisionAvoidance` prop:** Verify `side: 'none'` and `fallbackAxisSide: 'none'` are passed.

   d. **Assert all 15 songset names are rendered** (sanity check — not a visual clipping test, but confirms no items are dropped from the DOM).

2. **`popover.tsx` unit tests (new or existing):**
   - Render `PopoverContent` with `side="top"` and `collisionAvoidance={{ side: 'none' }}`.
   - Assert the `PopoverPrimitive.Positioner` receives these props (not the `Popup`).
   - Assert the positioner's className contains `z-[70]` (not `z-50`).

3. **Existing v2 test retention:** Keep the existing tests for ARIA roles, focus-on-mount guard, and className assertions (`max-h-64` / `overflow-y-auto` absence).

4. **Run commands:**
   ```bash
   cd delivery/webapp && pnpm test -- --run src/test/components/audio/AudioPlayerBar.test.tsx
   cd delivery/webapp && pnpm lint && pnpm typecheck
   ```

## Live DevTools Evidence (from this investigation)

### Measurement with 20 items (mocked fetch, data-side="right")

```json
{
  "positioner": {
    "zIndex": "50",
    "className": "z-50",
    "rect": { "top": 6, "bottom": 727, "height": 721 }
  },
  "popup": {
    "top": 6, "bottom": 727, "height": 721,
    "scrollHeight": 1059, "clientHeight": 719,
    "maxHeight": "721px", "overflowY": "auto"
  },
  "playerBar": {
    "top": 647, "bottom": 732, "height": 85,
    "zIndex": "60"
  },
  "overlap": 80,
  "hiddenItems": [
    { "index": 12, "coveredAmount": 49 },
    { "index": 13, "coveredAmount": 102 },
    { "index": 14, "coveredAmount": 155 },
    { "index": 15, "coveredAmount": 208 },
    { "index": 16, "coveredAmount": 261 },
    { "index": 17, "coveredAmount": 314 },
    { "index": 18, "coveredAmount": 367 },
    { "index": 19, "coveredAmount": 419 }
  ]
}
```
8 of 20 items hidden behind the player bar.

### Loading → Data transition (with 100ms sampling)

```
100ms:  data-side="top",    popupHeight=70  (loading spinner)
700ms:  data-side="top",    popupHeight=70  (still loading)
900ms:  data-side="right",  popupHeight=721 (data loaded, flip triggered)
```

The placement flips from "top" → "right" when content arrives. The `top` placement would have worked (available height 680px > content height), but `flip` is too aggressive: it considers the *unconstrained* natural content height (not the CSS-max-height-constrained height) when deciding whether to flip.

## Diagram

```
Viewport (732px tall)
┌─────────────────────────────┐
│                             │ ← popup top (6px)
│   ┌──────────────────┐      │
│   │ Songset 1        │      │
│   ├──────────────────┤      │
│   │ Songset 2        │      │
│   ├──────────────────┤      │
│   │ ...              │      │
│   ├──────────────────┤      │ ← player bar top (647px)
│   │ Songset 13 ████  │      │   ↑ These 8 items are
│   ├──────────────────┤      │   │ HIDDEN behind the
│   │ Songset 14 ████  │      │   │ player bar (z-60 > z-50)
│   ├──────────────────┤      │   │
│   │ Songset 20 ████  │      │   ↓
│   └──────────────────┘      │ ← popup bottom (727px)
│ ┌─────────────────────────┐ │
│ │ Player bar (z-60)       │ │ ← player bar bottom (732px)
│ │ ▶ Song  ♪     🔍 ⓘ  ✕  │ │
│ └─────────────────────────┘ │
└─────────────────────────────┘

After fix: data-side="top" forced, flip disabled

┌─────────────────────────────┐
│                             │ ← popup top (6px, capped by --available-height)
│   ┌──────────────────┐      │
│   │ Songset 1        │      │
│   ├──────────────────┤      │
│   │ Songset 2        │      │
│   ├──────────────────┤      │
│   │ ... (scrollable) │      │
│   ├──────────────────┤      │
│   │ Songset 20       │      │
│   └──────────────────┘      │ ← popup bottom (686px, trigger.y - 4px)
│ ┌─────────────────────────┐ │ ← player bar top (647px, no overlap!)
│ │ Player bar (z-60)       │ │  ↑ Popup is ABOVE this, not behind it
│ └─────────────────────────┘ │
└─────────────────────────────┘
```

## Implementation Order

1. Update `delivery/webapp/src/components/ui/popover.tsx` — destructure `side` / `collisionAvoidance`, forward to Positioner, change z-index.
2. Update `delivery/webapp/src/components/audio/LocateSongsetsPopover.tsx` — pass `side="top"` and `collisionAvoidance={{ side: 'none', fallbackAxisSide: 'none' }}`.
3. Run `pnpm lint` and `pnpm typecheck` from `delivery/webapp/`.
4. Update automated tests as described.
5. Manual DevTools verification (1/12/15/20 items, lyrics open, short viewport).
6. Commit and push (`git pull --rebase && git push`).

## Notes

- Base UI `collisionAvoidance` prop: type `SideFlipMode` with `side: 'flip' | 'none'`, `align: 'flip' | 'shift' | 'none'`, `fallbackAxisSide: 'start' | 'end' | 'none'`. Default `side: 'flip'`, `align: 'flip'`, `fallbackAxisSide: 'end'`.
- Setting `collisionAvoidance.side = 'none'` sets the `flip` middleware to `null` in `useAnchorPositioning.js`, completely disabling flip behavior for the side axis.
- The `size` middleware (which sets `--available-height`) is always pushed to the middleware array regardless of `collisionAvoidance` settings. So even with `side: 'none'`, the CSS `max-h-(--available-height)` correctly constrains the popup height.
- The `shift` middleware (for the align axis) is also affected by `collisionAvoidance`. With `side: 'none'` but default `align: 'flip'`, alignment can still shift/flip on the horizontal axis. This is desirable — we want the popup to shift left/right to stay in the viewport.
