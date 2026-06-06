# Songset Card: Prominent Play Button + Mobile KAB UX v2

## Summary

Improve the Songsets List screen (`/songsets`) so worship playback is easy to find after a fresh successful render, and so the kebab menu is discoverable on touch/mobile browsers. This version keeps Play available in the menu as a fallback, promotes it only for fresh completed renders, and avoids using viewport width as a proxy for touch capability.

## UX Requirements

- Show a prominent card-level `Play` button only when the songset has a fresh successful render:
  ```ts
  renderState === "fresh" && !!lastCompletedRenderJobId && !!onPlay
  ```
- Do not show the prominent Play button for `unrendered`, `rendering`, `failed`, or `stale` songsets.
- Keep the existing `Play` item in the kebab menu for consistency and secondary access.
- Make the kebab menu trigger visible on touch/non-hover browsers without requiring users to guess where to tap.
- Preserve desktop mouse behavior for the kebab trigger: secondary actions may stay hover-revealed, but must remain visible on keyboard focus and while the menu is open.
- Protect title readability on mobile. The Play button and kebab trigger must not squeeze long songset names into an unusable width.

## Implementation Changes

### `webapp/src/components/songset/SongsetRow.tsx`

Add a derived boolean near the existing formatting helpers:

```tsx
const canPlayFreshRender =
  renderState === "fresh" && Boolean(lastCompletedRenderJobId) && Boolean(onPlay);
```

Render a prominent Play button when `canPlayFreshRender` is true:

```tsx
{canPlayFreshRender && (
  <Button
    variant="default"
    size="sm"
    className="shrink-0 gap-1.5"
    onClick={onPlay}
  >
    <Play className="size-4" />
    Play
  </Button>
)}
```

Keep the existing `DropdownMenuItem` for Play unchanged:

```tsx
<DropdownMenuItem onClick={onPlay}>
  <Play className="size-4 mr-2" />
  Play
</DropdownMenuItem>
```

Change the kebab trigger class from hover-only visibility:

```tsx
shrink-0 opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity
```

to hover-capability-aware visibility:

```tsx
shrink-0 opacity-100 [@media(hover:hover)]:opacity-0 [@media(hover:hover)]:group-hover:opacity-100 focus:opacity-100 data-[state=open]:opacity-100 transition-opacity
```

Use a responsive action layout that keeps the songset title readable:

- Keep title and description in a `min-w-0` content area.
- Place card actions in a separate shrink-wrapped action area.
- On narrow layouts, allow the action area to wrap below the title/description or use a second compact action row.
- Avoid nesting buttons inside `Link`; the Play button should be a sibling action, so no `preventDefault` or `stopPropagation` is needed unless a future clickable-card wrapper is introduced.

No API, routing, schema, or data-fetching changes are required. `SongsetList.tsx` already passes `renderState`, `lastCompletedRenderJobId`, and `onPlay` through to `SongsetRow`.

## Test Plan

Update `webapp/src/test/components/songset/SongsetRow.test.tsx`:

- Fresh render with `lastCompletedRenderJobId` shows the prominent card-level `Play` button.
- Fresh render without `lastCompletedRenderJobId` does not show the prominent card-level `Play` button.
- `stale`, `unrendered`, `rendering`, and `failed` rows do not show the prominent card-level `Play` button.
- Clicking the prominent card-level `Play` button calls `onPlay`.
- The kebab dropdown remains accessible and still contains the `Play` menu item.
- The kebab trigger includes the touch-visible and hover-capability-aware visibility classes.

Update `webapp/src/test/components/songset/SongsetList.test.tsx` only if callback wiring needs explicit coverage for the new card-level Play button.

Run:

```bash
cd webapp && pnpm test src/test/components/songset/SongsetRow.test.tsx
cd webapp && pnpm test src/test/components/songset/SongsetList.test.tsx
cd webapp && pnpm lint
```

After implementation changes in a future coding step, run:

```bash
graphify update .
```

## Assumptions

- "Successful render" means a fresh render with an available completed render job.
- Stale completed outputs should remain playable through secondary paths, but should not be promoted as the primary worship action.
- The kebab menu remains the complete secondary action list.
