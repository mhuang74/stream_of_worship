# Fix: Webapp Player — Locate Songsets Popover Clipping (v2)

## Status

Spec reviewed and revised. Supersedes `fix-webapp-player-locate-songsets-popover-clipping.md` (v1). **Do not implement** until approved.

## Revision History

| Version | Date | Changes |
|---------|------|---------|
| v1 | — | Initial investigation and root-cause analysis. |
| v2 | 2026-08-07 | Reviewed against source code and Base UI internals. Four revisions: (1) Tailwind v4 shorthand syntax, (2) zero blast radius confirmation, (3) scoped-in pre-existing ARIA roles + focus-on-mount bug, (4) jsdom-aware test strategy. |

## Summary

The "Find containing songsets" popover in the Audio Player bar clips the last result row. When many songsets contain the current song, only the top portion of the final row is visible. This is caused by a hard-coded `max-h-64` on the inner list container combined with a shared `PopoverContent` wrapper that references a non-existent CSS custom property.

Additionally, two pre-existing issues are scoped into this fix:
- Incorrect `role="listbox"` / `role="option"` ARIA semantics on what is actually a navigation list.
- A `useEffect` that refocuses the trigger button on initial mount, stealing focus on page load.

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
| With 6 injected results | listbox `scrollHeight: 317px`, `clientHeight: 256px` | 61 px of content (~ the last row) is hidden. |

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

### 1. `popover.tsx` uses non-existent CSS custom properties

Base UI's `PopoverPositionerCssVars.js` defines:
- `--available-height` (set by the `size` middleware in `useAnchorPositioning.js:221`)
- `--transform-origin` (set by the `transformOrigin` middleware in `useAnchorPositioning.js:272`)

The wrapper at `popover.tsx:45` references `--popover-available-height` and `--popover-transform-origin`, which do not exist in any Base UI CSS variable definition. Both resolve to `none` / fallback, disabling viewport-aware sizing and transform-origin alignment.

The sibling components `select.tsx:86` and `dropdown-menu.tsx:54` already use the correct variables, confirming this is an oversight specific to `popover.tsx`.

### 2. `LocateSongsetsPopover.tsx` hard-caps the list at `max-h-64`

The inner container at line 123 (`flex flex-col max-h-64 overflow-y-auto`) creates a second, arbitrary 256 px scroll boundary. When the natural height of the rows exceeds 256 px, the last row is clipped without any relationship to the real viewport space.

### 3. Double scroll containers

Both the outer `PopoverContent` popup and the inner listbox declare `overflow-y-auto`. The inner `max-h-64` wins and becomes the clipping boundary.

### 4. Pre-existing: incorrect ARIA roles

`LocateSongsetsPopover.tsx:121-129` uses `role="listbox"` on the container and `role="option"` / `aria-selected={false}` on each row. These are semantics for a form-control listbox (e.g., a `<select>` replacement). However, the buttons navigate to a songset page via `router.push()` — they do not select a value. The correct pattern is `role="list"` / `role="listitem"`, or omission of explicit roles entirely (the `<button>` elements already have implicit semantics).

### 5. Pre-existing: focus stolen on page mount

`LocateSongsetsPopover.tsx:84-88` has a `useEffect` that fires `triggerRef.current.focus()` whenever `open` is `false`. On initial mount (before the popover has ever been opened), this effect runs and focuses the map-pin button, stealing focus from whatever the user was doing on page load.

## Proposed Fix

### Change 1: Fix CSS custom property references in `popover.tsx`

In `delivery/webapp/src/components/ui/popover.tsx` line 45, replace:
- `max-h-[var(--popover-available-height)]` → `max-h-(--available-height)`
- `origin-[var(--popover-transform-origin)]` → `origin-(--transform-origin)`

Use the Tailwind v4 shorthand syntax `(--var-name)` to match the convention already used in `select.tsx:86` and `dropdown-menu.tsx:54`. This is not just cosmetic — the v3 arbitrary syntax `max-h-[var(--name)]` generates `max-height: var(--name)` which works, but the v4 shorthand `max-h-(--name)` is the established pattern in this codebase and avoids inconsistency.

Result: the popup's `max-height` resolves to the pixel value set by Base UI's `size` middleware, and `transform-origin` resolves to the value set by the `transformOrigin` middleware.

### Change 2: Remove inner scroll cap in `LocateSongsetsPopover.tsx`

In `delivery/webapp/src/components/audio/LocateSongsetsPopover.tsx` line 123, change:
```tsx
className="flex flex-col max-h-64 overflow-y-auto"
```
to:
```tsx
className="flex flex-col"
```

The popup itself (via `PopoverContent`) already has `overflow-y-auto` and now has a correct `max-height`. No inner scroll boundary is needed. Since `PopoverContent` is used with `className="w-72 p-0"`, row edges align with the popover border, which is visually correct.

### Change 3: Fix ARIA roles

In `LocateSongsetsPopover.tsx`:
- Line 121: Change `role="listbox"` to `role="list"`.
- Line 128: Change `role="option"` to `role="listitem"`.
- Remove `aria-selected={false}` (line 129) — it is not meaningful for navigation items; `aria-current` would be appropriate if highlighting the current songset, but that is out of scope.

### Change 4: Guard focus-restore against initial mount

In `LocateSongsetsPopover.tsx` lines 84-88, add a ref guard so focus is only restored after the popover has been opened and then closed — not on initial mount:

```tsx
const hasOpenedRef = useRef(false);

useEffect(() => {
  if (open) {
    hasOpenedRef.current = true;
  } else if (hasOpenedRef.current && triggerRef.current) {
    triggerRef.current.focus();
  }
}, [open]);
```

This ensures the trigger is only refocused after the user has interacted with the popover.

## Files to Modify

| File | Change |
|------|--------|
| `delivery/webapp/src/components/ui/popover.tsx` | Fix `max-h-*` and `origin-*` CSS variable names to match Base UI's `--available-height` / `--transform-origin`; use Tailwind v4 shorthand syntax. |
| `delivery/webapp/src/components/audio/LocateSongsetsPopover.tsx` | Remove `max-h-64 overflow-y-auto` from the list container; fix ARIA roles `listbox`→`list`, `option`→`listitem`, remove `aria-selected`; guard focus-restore `useEffect` against initial mount. |

## Blast Radius Analysis

**Zero blast radius.** A grep for `<PopoverContent` across `delivery/webapp/src/` confirms that `LocateSongsetsPopover.tsx` is the **only consumer** of the `PopoverContent` component in the entire codebase. The `popover.tsx` fix cannot affect any other component because no other component imports or renders `PopoverContent`.

The `Popover`, `PopoverTrigger`, and `PopoverClose` exports are also unused elsewhere (no grep hits outside of `popover.tsx` itself and the import in `LocateSongsetsPopover.tsx`).

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
5. On page load (before clicking the map-pin), inspect document.activeElement.
   - Expect: `<body>` or the user's intended focus target, NOT the map-pin button.
6. Open the popover, then press Escape to close it. Inspect document.activeElement.
   - Expect: the map-pin trigger button is focused (focus restored after close).
7. Inspect ARIA roles in DevTools.
   - Expect: container has `role="list"`, rows have `role="listitem"`, no `aria-selected` attribute.

### Automated tests

**jsdom limitation acknowledged:** jsdom does not apply CSS layout, so `max-height` has no visual effect and all DOM nodes render regardless of clipping. Therefore, the test strategy focuses on asserting className changes (the regression guard) rather than visual clipping behavior.

1. **`AudioPlayerBar.test.tsx`** — Add a `LocateSongsetsPopover` test block:
   - Mock `global.fetch` to return 6 songset results.
   - Assert the list container does NOT have `max-h-64` or `overflow-y-auto` in its className.
   - Assert all 6 songset names are rendered in the document (sanity check that the fetch mock and rendering work).
   - Assert the container has `role="list"` and rows have `role="listitem"`.
   - Assert no element has `aria-selected`.
   - Note: fetch mocking (`vi.mock` for `global.fetch`) needs to be set up from scratch — the existing test file has no `LocateSongsetsPopover` coverage at all.

2. **Focus-on-mount regression test:**
   - Render `AudioPlayerBar` with a loaded song track.
   - Assert `document.activeElement` is NOT the map-pin button on initial render.
   - Click the map-pin to open, then press Escape to close.
   - Assert `document.activeElement` IS the map-pin button (focus restored after close).

3. **`popover.tsx` unit guard (optional):**
   - Render `PopoverContent` and assert its className contains `max-h-(--available-height)` and `origin-(--transform-origin)` (not the broken `--popover-` prefixed variants).

4. Run the existing suite:
   ```bash
   cd delivery/webapp && pnpm test -- --run src/test/components/audio/AudioPlayerBar.test.tsx
   ```

5. Run lint and typecheck:
   ```bash
   cd delivery/webapp && pnpm lint && pnpm typecheck
   ```

## Risks & Considerations

- **Zero blast radius** — `LocateSongsetsPopover` is the sole consumer of `PopoverContent`. No other component can be affected by the `popover.tsx` change.
- **Scrolling surface shift** — Removing the inner `max-h-64` means the popup (not the inner listbox) becomes the scrolling surface. Since the popup has `p-0` in this component, row edges align with the popover border, which is visually correct.
- **ARIA role change** — Switching from `listbox`/`option` to `list`/`listitem` changes the accessibility tree. This is strictly more correct: screen readers will announce the content as a navigable list rather than a form control. No assistive technology should have been relying on `listbox` semantics for navigation buttons.
- **Focus guard** — The `hasOpenedRef` pattern is a minimal, well-understood guard. It does not change behavior for the normal open → close cycle; it only prevents the spurious focus on initial mount.
- **Keyboard focus** — Keyboard navigation within a scrolling popup is standard Base UI behaviour; no extra roving-tabindex work is required beyond the existing per-row `onKeyDown` handlers.

## Implementation Order

1. Update `delivery/webapp/src/components/ui/popover.tsx` — fix CSS variable names.
2. Update `delivery/webapp/src/components/audio/LocateSongsetsPopover.tsx` — remove inner scroll cap, fix ARIA roles, guard focus-restore effect.
3. Run `pnpm lint` and `pnpm typecheck` from `delivery/webapp/`.
4. Write and run automated tests as described above.
5. Manual DevTools verification as described.
6. Commit and push (`git pull --rebase && git push`).

## Notes

- Base UI variable reference: `PopoverPositionerCssVars.js` in `@base-ui/react/popover/positioner/` defines `availableHeight = "--available-height"` and `transformOrigin = "--transform-origin"`. The `size` middleware in `useAnchorPositioning.js:221` sets `--available-height` on the floating positioner element. The `transformOrigin` middleware at `useAnchorPositioning.js:272` sets `--transform-origin`.
- Sibling components already using the correct variables: `select.tsx:86` (`max-h-(--available-height) origin-(--transform-origin)`) and `dropdown-menu.tsx:54` (same).
- The `usePopupAutoResize.js` hook temporarily overrides `--available-height` to `max-content` during layout measurement, then restores it. This is internal to Base UI and does not affect the fix.
- Tailwind v4 shorthand `max-h-(--available-height)` generates the same CSS as v3 arbitrary `max-h-[var(--available-height)]` but matches the codebase convention in `select.tsx` and `dropdown-menu.tsx`.
