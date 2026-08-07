# Fix: Webapp Player — Locate Songsets Popover Clipping

## Status

Planning / investigation complete. **Do not implement** until this spec is reviewed.

## Summary

The "Find containing songsets" popover in the Audio Player bar clips the last result row. When many songsets contain the current song, only the top portion of the final row is visible. This is caused by a hard-coded `max-h-64` on the inner list container combined with a shared `PopoverContent` wrapper that references a non-existent CSS custom property.

## Reproduction

1. Open the webapp and start playback of any song that appears in multiple songsets.
2. Click the **map-pin** icon in the player bar (labelled "Find containing songsets").
3. If the song appears in ~5 or more songsets, the bottom of the last row is clipped.

## Live DevTools findings

Inspected on Desktop Chrome with the popover open:

| Element | Key computed value | Meaning |
|---------|-------------------|---------|
| Positioner (`role="presentation"`) | `--available-height: 728px` | Base UI correctly exposes the vertical room above the bottom player bar. |
| Popup (`data-slot="popover-content"`) | `max-height: none` | The intended `max-h-[var(--popover-available-height)]` rule is no-op because Base UI sets `--available-height`, not `--popover-available-height`. |
| Inner listbox (`role="listbox"`) | `max-height: 256px` | Hard-coded `max-h-64` caps the list. |
| With 6 injected results | listbox `scrollHeight: 317px`, `clientHeight: 256px` | 61 px of content (≈ the last row) is hidden. |

Injecting extra rows confirmed the symptom:
```text
rowCount: 6
listboxHeight: 256px
listboxScrollHeight: 317
listboxClientHeight: 256
popupHeight: 258px
popupMaxHeight: none
```

## Root Cause

1. **`src/components/ui/popover.tsx` uses the wrong CSS variable.**
   Base UI's `PopoverPositioner` sets `--available-height` on the floating container. The wrapper instead writes `max-h-[var(--popover-available-height)]`, which resolves to `none`. This disables viewport-aware sizing that Base UI uses for collision/flip behaviour.

2. **`LocateSongsetsPopover.tsx` hard-caps the list at `max-h-64`.**
   The inner listbox (`flex flex-col max-h-64 overflow-y-auto`) creates a second, arbitrary scroll boundary. When the natural height of the rows exceeds 256 px, the last row is clipped without any relationship to the real viewport space.

3. **Double scroll containers.**
   Both the outer `PopoverContent` popup and the inner listbox declare `overflow-y-auto`. The inner `max-h-64` wins and becomes the clipping boundary.

## Proposed Fix

### Option A: Use Base UI's available height + let the popup scroll (recommended)

Matches the stated requirement "expand to show all matching songsets up to a reasonable viewport max, then scroll".

1. In `src/components/ui/popover.tsx`:
   - Change `max-h-[var(--popover-available-height)]` to `max-h-[var(--available-height)]`.
   - Remove the also-incorrect `origin-[var(--popover-transform-origin)]` class, or replace it with `origin-[var(--transform-origin)]` (Base UI sets `--transform-origin`). Note: `transform-origin` currently falls back to `center`, which is acceptable; however, using the correct variable keeps the wrapper aligned with Base UI.

2. In `src/components/audio/LocateSongsetsPopover.tsx`:
   - Remove `max-h-64 overflow-y-auto` from the listbox container.
   - Keep `flex flex-col` so the rows stack vertically.
   - Keep `role="listbox"` and `role="option"` semantics.

Result: the popup grows with the number of results until it hits `--available-height`, then the popup itself scrolls.

### Option B: Keep the inner list scrollable but bind it to available height

If future designs require the listbox itself to be the scrolling surface (e.g., sticky header inside the popover), set:

```tsx
className="flex flex-col overflow-y-auto max-h-[min(theme(spacing.64),var(--available-height))]"
```

This is **not** recommended for the current simple list because it still imposes an arbitrary 256 px cap and is more complex.

## Files to Modify

| File | Change |
|------|--------|
| `delivery/webapp/src/components/ui/popover.tsx` | Use `max-h-[var(--available-height)]`; correct the `origin-*` variable name. |
| `delivery/webapp/src/components/audio/LocateSongsetsPopover.tsx` | Remove `max-h-64 overflow-y-auto` from the listbox wrapper. |

## Testing Plan

### Manual / DevTools verification

1. Open the player and click the map-pin icon for a song in 1 songset.
   - Expect: single row fully visible, no scrollbar, no bottom clipping.
2. Repeat for a song in 5+ songsets (or temporarily inject rows via DevTools).
   - Expect: all rows visible up to the viewport ceiling, then a single scrollbar on the popup.
3. Shrink the browser window to a short mobile height and repeat.
   - Expect: popover flips/constrains to available space and scrolls.
4. Inspect `data-slot="popover-content"` in DevTools.
   - Expect: `max-height` resolves to a pixel value based on `--available-height`, not `none`.

### Automated tests

1. **`AudioPlayerBar.test.tsx` / `LocateSongsetsPopover` test**: mock `/api/songs/:id/songsets` to return 6 results and assert all result names are rendered (no clipping of last row). Currently only loading/error states are implicitly covered; add a rendered-row assertion.
2. **Visual regression / Storybook** (if available): render the popover with 1, 5, and 10 results to prevent regressions.
3. Run the existing suite:
   ```bash
   cd delivery/webapp && pnpm test -- --run src/test/components/audio/AudioPlayerBar.test.tsx
   ```

## Risks & Considerations

- `popover.tsx` is shared by all popovers in the app. Fixing the variable name makes the shared wrapper behave as originally intended, but any consumer that relied on the broken `none` max-height could see a layout change. Review other popover usages is recommended; no consumer should depend on a popup that ignores viewport space.
- Removing the inner `max-h-64` means the popup (not the inner listbox) becomes the scrolling surface. Since the popup has `p-0` in this component, row edges align with the popover border, which is visually correct.
- Keyboard focus within a scrolling dialog is standard Base UI behaviour; no extra roving-tabindex work is required beyond the existing per-row `onKeyDown` handlers.

## Implementation Order

1. Update `src/components/ui/popover.tsx`.
2. Update `src/components/audio/LocateSongsetsPopover.tsx`.
3. Run `pnpm lint` and `npx tsc --noEmit`.
4. Run tests above and fix any assertions that depend on exact DOM structure.
5. Manual DevTools verification as described.
6. Commit and push (`git pull --rebase && git push`).

## Notes

- Base UI variable reference: `useAnchorPositioning.js` in `@base-ui/react/utils` sets `--available-height` and `--transform-origin` on the floating positioner.
- The original clipping screenshot showed a single row apparently cropped at the bottom; in the live session the same happened once enough rows were present. The unifying cause is the 256 px inner cap combined with the no-op outer max-height.
