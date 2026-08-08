# Fix: Webapp Player — Locate Songsets Popover Clipping (v4)

## Status

Spec written, **do not implement** until reviewed. Supersedes v3 (which proposed a global z-index change). This v4 reflects the decision to **scope the z-index fix to `LocateSongsetsPopover` only**.

## Revision History

| Version | Date | Key change |
|---------|------|------------|
| v1 | — | Initial root cause: wrong CSS var + inner `max-h-64`. |
| v2 | 2026-08-07 | Fixed CSS var names, removed inner cap, ARIA + focus guards. Implemented as commit c433baa4. |
| v3 | 2026-08-08 | New root cause: z-index conflict + placement flip. Proposed global `z-50`→`z-[70]` + force `side="top"`/disable flip. |
| v4 | 2026-08-08 | **Scoped** z-index fix to `LocateSongsetsPopover` only (per review decision). Keeps default `z-50` for the shared `PopoverContent`. |

## Summary

The last "Songset Containing This Song" result row(s) are hidden **behind the fixed bottom player bar**. The popover's positioner has `z-index: 50`, but the player bar has `z-index: 60`, so the player paints over the popover's bottom rows. This reproduces at **all** viewports because the trigger button lives *inside* the fixed player bar and the popover opens anchored above it.

## Root Cause (confirmed via Chrome DevTools)

- Player bar: `fixed bottom-0 z-[60]` — `src/components/audio/AudioPlayerBar.tsx:160`
- Popover positioner: `z-50` — `src/components/ui/popover.tsx:40`
- The `Find in songsets` trigger is inside the player bar; the popover opens above it.
- Because `z-50 < z-60`, the player bar renders on top of the popover's bottom rows.

Measured at 820×480 (7 real results, song `不停讚美祢` in 7 songsets):

```
Viewport: 480px tall
Player bar: top=395, bottom=480, height=85, z-index=60
Trigger:   top=438, bottom=466 (inside player bar)
Popover:   top=62,  bottom=434, height=372, z-index=50
Overlap:   434 - 395 = 39px of the popover's bottom (last row) is behind the player bar
```

`elementFromPoint(362, 415)` in the overlap region returns a player-bar slider element, confirming the player is painted on top of the popover.

The base `--available-height` (from Base UI) is the full viewport space above the anchor; it does **not** subtract the fixed player's height, so list content falls into the player's region. Even at tall viewports (1280×776), the popover bottom (722) overlaps the player top (~691), so the last row is partially covered.

## Decision (from review)

- **Approach:** Raise the popover's z-index above the player (`z-[70]`).
- **Scope:** Apply to `LocateSongsetsPopover` **only**. Do **not** change the default z-index of the shared `PopoverContent` (other popovers/selects stay at `z-50`).
- **Viewports:** Must work at all viewports (tall and short).

## Proposed Fix

### Change 1: `src/components/ui/popover.tsx` — make the positioner z-index overridable

The `PopoverContent` wrapper currently hardcodes `className="z-50"` on the `PopoverPrimitive.Positioner` (line 40) and merges the caller's `className` onto the inner `Popup` (line 45), not the positioner. To scope a z-index override to a single consumer, expose a way to set the positioner's z-index.

Add an optional `positionerClassName` prop (default `"z-50"`) and apply it to the `Positioner`:

```tsx
function PopoverContent({
  className,
  align = "center",
  sideOffset = 4,
  positionerClassName = "z-50",
  ...props
}: PopoverPrimitive.Popup.Props &
  Pick<PopoverPrimitive.Positioner.Props, "align" | "sideOffset" | "side"> & {
    positionerClassName?: string;
  }) {
  return (
    <PopoverPrimitive.Portal>
      <PopoverPrimitive.Positioner
        align={align}
        sideOffset={sideOffset}
        className={positionerClassName}
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
  )
}
```

Default remains `z-50`, so existing behavior is unchanged for any other consumer.

### Change 2: `src/components/audio/LocateSongsetsPopover.tsx` — raise z-index above the player

Pass `positionerClassName="z-[70]"` to `PopoverContent` so the popover renders above the player bar (`z-[60]`):

```tsx
<PopoverContent
  className="w-72 p-0"
  align="start"
  positionerClassName="z-[70]"
>
```

Keep the existing `max-h-(--available-height)` + `overflow-y-auto` cap (from the shared `PopoverContent`) — it still protects against overflowing the top of short viewports and provides internal scrolling.

### Change 3: No change to the list container

The v2 removal of `max-h-64 overflow-y-auto` from the list container is retained. No further changes needed there.

## Files to Modify

| File | Change |
|------|--------|
| `delivery/webapp/src/components/ui/popover.tsx` | Add optional `positionerClassName` prop (default `"z-50"`), apply to `Positioner`. |
| `delivery/webapp/src/components/audio/LocateSongsetsPopover.tsx` | Pass `positionerClassName="z-[70]"` to `PopoverContent`. |

## Blast Radius Analysis

- `PopoverContent` has one consumer in the codebase: `LocateSongsetsPopover.tsx` (confirmed via grep). The new `positionerClassName` prop defaults to `z-50`, so even if more consumers are added later, behavior is unchanged unless they opt in.
- The z-index override is applied only in `LocateSongsetsPopover`, so no other popover/select/dropdown is affected.
- No database/schema changes; purely presentational CSS.

## Testing Plan

### Manual / DevTools verification

1. **Real data (7 results):** Open the popover for `不停讚美祢` (in 7 songsets) at a tall viewport (e.g. 1280×776) and a short viewport (e.g. 820×480).
   - Expect: all 7 rows fully visible; last row (`絕望與恩典_浪子回頭`) is **not** covered by the player bar.
   - Use `elementFromPoint` in the former overlap region (e.g. y≈415 at 820×480) — it should now return a popover listitem, not a player element.
2. **Z-index check:** Inspect the positioner element.
   - Expect: computed `z-index` is 70 (not 50). Player bar is 60. Popover renders above player.
3. **Many results (stress):** Temporarily mock fetch to return 15–20 songsets.
   - Expect: popover caps at `--available-height`, scrolls internally, and the bottom of the scroll area is fully visible above the player bar (not hidden behind it).
4. **Lyrics panel open + popover:** Open lyrics panel (`L`), then open the popover.
   - Expect: popover still renders above the player bar; available height reduced; scrolls internally.
5. **Other popovers unaffected:** Confirm any other popover/select still renders at `z-50` (no regression).

### Automated tests

**jsdom limitation:** jsdom does not apply CSS layout or z-index, so tests assert prop forwarding / className rather than visual stacking.

1. **`AudioPlayerBar.test.tsx` — `LocateSongsetsPopover` block:**
   - Assert `PopoverContent` receives `positionerClassName="z-[70]"` (e.g. spy on `PopoverContent` or assert rendered output includes the prop).
   - Keep existing assertions (list has no `max-h-64`/`overflow-y-auto`, `role=list`/`listitem`, no `aria-selected`, focus guards).
2. **`popover.tsx` unit guard (optional):**
   - Render `PopoverContent` without `positionerClassName` → positioner className contains `z-50`.
   - Render with `positionerClassName="z-[70]"` → positioner className contains `z-[70]`.
3. **Run commands:**
   ```bash
   cd delivery/webapp && pnpm test -- --run src/test/components/audio/AudioPlayerBar.test.tsx
   cd delivery/webapp && pnpm lint && pnpm typecheck
   ```

## Risks & Considerations

- **Scoped z-index** avoids the v3 concern of globally raising all popovers above the player. Only `LocateSongsetsPopover` is raised.
- **Visual overlap:** With z-index raised, the popover's bottom rows render *on top of* the player bar in the overlap zone rather than being hidden. This is the intended "not clipped" behavior and matches how menus/selects overlay surrounding UI. If a cleaner separation is later desired, forcing `side="top"`/disabling flip (v3 Change 3) can be added as a follow-up — out of scope for this decision.
- **`--available-height`** still does not subtract the player's height; the z-index fix is what makes the covered rows visible. The existing `max-h-(--available-height)` cap remains for top-of-viewport overflow.

## Implementation Order

1. Update `delivery/webapp/src/components/ui/popover.tsx` — add `positionerClassName` prop.
2. Update `delivery/webapp/src/components/audio/LocateSongsetsPopover.tsx` — pass `positionerClassName="z-[70]"`.
3. Run `pnpm lint` and `pnpm typecheck` from `delivery/webapp/`.
4. Update/add automated tests as described.
5. Manual DevTools verification (real 7 results at tall + short viewports, stress test, lyrics open).
6. Commit and push (`git pull --rebase && git push`).

## Notes

- Player bar z-index: `z-[60]` (`AudioPlayerBar.tsx:160`).
- Popover positioner default z-index: `z-50` (`popover.tsx:40`).
- `z-[70]` is chosen to be strictly above the player's `z-[60]` and below the full-screen controller overlay (`z-[70]` in `ControllerPlayer.tsx:864` — verify no conflict; if needed use `z-[65]` or `z-[80]`).
