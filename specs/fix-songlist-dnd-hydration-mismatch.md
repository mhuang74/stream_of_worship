# Fix SongList DnD Hydration Mismatch

## Summary

The hydration warning on the songset editor is caused by `@dnd-kit/core` generating an unstable accessibility id for `DndContext`. The server-rendered drag handle can receive an `aria-describedby` value such as `DndDescribedBy-1`, while the client hydration pass can generate `DndDescribedBy-0`.

This is not a backend data mismatch. The route server-fetches stable songset data, but the `SongList` drag-and-drop subtree is still SSR-rendered as part of the initial Next.js page.

## Implementation Plan

- Update `delivery/webapp/src/components/songset/SongList.tsx`.
- Import `useId` from React alongside the existing hooks.
- Inside `SongList`, create a stable id with `const dndContextId = useId();`.
- Pass that id into dnd-kit:

```tsx
<DndContext
  id={dndContextId}
  sensors={sensors}
  collisionDetection={closestCenter}
  onDragEnd={handleDragEnd}
>
```

- Do not disable SSR for the songset editor.
- Do not change backend routes, database queries, or songset data serialization.
- Do not hardcode a single global id, because `SongList` may appear more than once in future UI.

## Test Plan

- Update the `@dnd-kit/core` mock in `src/test/components/songset/SongList.test.tsx` so it captures the `DndContext` `id` prop.
- Add a focused test that rendering `SongList` passes a non-empty stable id to `DndContext`.
- Keep existing behavior tests for rendering, playback buttons, delete confirmation, and accessibility labels.
- Run:

```bash
pnpm --filter sow-webapp test -- src/test/components/songset/SongList.test.tsx
pnpm --filter sow-webapp test -- src/test/accessibility/accessibility.test.tsx
pnpm --filter sow-webapp lint
```

## Acceptance Criteria

- Browser console no longer reports a hydration mismatch for `aria-describedby` on the SongList drag handle.
- Drag-and-drop reordering still works with pointer and keyboard sensors.
- The drag handle still has the expected accessible name, and dnd-kit still provides screen reader instructions through the stable id.
- No backend behavior changes are introduced.

## Follow-up

- After implementation, run `graphify update .` because code files will have changed.
- Complete the repository-required push flow after the implementation commit: `git pull --rebase`, `git push`, and `git status`.
