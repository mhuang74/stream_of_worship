# Fix: Webapp Player — Locate Songsets Popover Clipping (v5)

## Status

Spec written, **do not implement** until reviewed. Supersedes v3 and v4.

## Revision History

| Version | Date | Key change |
|---------|------|------------|
| v1 | — | Initial root cause: wrong CSS var + inner `max-h-64`. |
| v2 | 2026-08-07 | Fixed CSS var names, removed inner cap, ARIA + focus guards. Implemented as commit c433baa4. |
| v3 | 2026-08-08 | New root cause: z-index conflict + placement flip. Proposed global `z-50`→`z-[70]` + force `side="top"`/disable flip. Implemented by PR #138. |
| v4 | 2026-08-08 | **Scoped** z-index fix to `LocateSongsetsPopover` only via `positionerClassName` prop. Proposed but not implemented. |
| v5 | 2026-08-08 | Incorporates PR #138 review findings. Adopts v4's scoped `positionerClassName` approach with `z-[65]` (not `z-[70]`). Drops prop-spying test pattern. Adds min-height floor and Safari runbook. |

## Problem Statement

PR #138 (head `8870cfaf`, branch `fix_songset_containing_search`) shipped the v3 implementation of the popover clipping fix. The v3 root cause investigation (see `specs/...v3.md` for the full DevTools evidence) identified a z-index conflict: the popover positioner was `z-50`, the player bar was `z-[60]`, and Floating UI's `flip` middleware relocated the popup to `side="right"` when content exceeded ~680px, causing ~80px of bottom rows to be hidden behind the player bar.

PR #138's fix contained two correct changes and two problems:

**Correct:** (1) Destructured `side` and `collisionAvoidance` and forwarded them to `PopoverPrimitive.Positioner` — this was a real bug where `side` was silently dropped onto `Popup` and ignored. (2) Pinned `side="top"` and disabled flip via `collisionAvoidance={{ side: "none", fallbackAxisSide: "none" }}` in `LocateSongsetsPopover` — prevents the flip-to-right overlap.

**Problems:** (1) Globally changed the positioner className from `z-50` to `z-[70]` on the shared `PopoverContent` primitive — collides with `ControllerPlayer` (also `z-[70]`) and inverts the modal layering contract (all other overlays are `z-50`). (2) Added a fragile prop-spying test pattern in `AudioPlayerBar.test.tsx` that breaks on internal refactors.

This v5 spec addresses the two problems and adds hardening (min-height floor, Safari runbook, regression guard).

## What PR #138 Got Right

- **Prop forwarding** (`side`, `collisionAvoidance` forwarded to `Positioner`) — was a real bug where `side` fell into `...props` and was spread onto `Popup` (which ignores it). Fix is correct.
- **`side="top"` + `collisionAvoidance={{ side: "none", fallbackAxisSide: "none" }}`** for `LocateSongsetsPopover` — correct. Prevents the flip-to-right overlap that caused most of the clipping in v3's DevTools measurements.

## What PR #138 Got Wrong

- **Global `z-[70]`** on shared `PopoverContent` (`popover.tsx:47`). `ControllerPlayer.tsx:864` is `fixed inset-0 z-[70]`. Two elements at the same stacking level produce paint-order ambiguity (DOM-order dependent). Every other overlay primitive in the codebase is `z-50`; bumping the shared `PopoverContent` above them inverts the modal capture contract for any future consumer.
- **Prop-spying test** in `AudioPlayerBar.test.tsx:16–22, 847–901`. `vi.fn(actual.PopoverContent)` wraps the real component with a spy; assertions read `vi.mocked(PopoverContent).mock.calls[calls.length - 1][0]`. This breaks on internal refactors (e.g., the `positionerClassName` prop addition in this spec), relies on undocumented `calls.length - 1` indexing, and installs the spy module-wide.

## Z-Index Scale (Canonical Reference)

This is the single source of truth for stacking decisions:

| Tier | z-index | Element |
|------|---------|---------|
| Standard overlays | `z-50` | Dialog, Sheet, DropdownMenu, Select, Tooltip, AlertDialog, Header, BottomNav, LyricJumpList, OfflineIndicator |
| Audio player bar | `z-[60]` | `AudioPlayerBar` (fixed bottom bar) |
| Player-bar popovers | `z-[65]` | `LocateSongsetsPopover` positioner (scoped override) |
| Fullscreen controller | `z-[70]` | `ControllerPlayer` (fullscreen cast / projection) |

When a new overlay needs a z-index, consult this table. Do not change the shared `PopoverContent` default.

## Changes

### Change 1: Revert global `z-[70]` → `z-50`; add scoped `positionerClassName`

**File:** `delivery/webapp/src/components/ui/popover.tsx`

Revert the positioner className from `"z-[70]"` back to the default `"z-50"`. Add an optional `positionerClassName?: string` prop (default `"z-50"`) that is applied to `PopoverPrimitive.Positioner`'s `className` instead of the hardcoded string. Keep PR #138's correct `side`, `collisionAvoidance`, `align`, `sideOffset` forwarding.

```tsx
function PopoverContent({
  className,
  align = "center",
  side,
  sideOffset = 4,
  collisionAvoidance,
  positionerClassName = "z-50",
  ...props
}: PopoverPrimitive.Popup.Props &
  Pick<
    PopoverPrimitive.Positioner.Props,
    "align" | "sideOffset" | "side" | "collisionAvoidance"
  > & {
    positionerClassName?: string;
  }) {
  return (
    <PopoverPrimitive.Portal>
      <PopoverPrimitive.Positioner
        align={align}
        side={side}
        sideOffset={sideOffset}
        collisionAvoidance={collisionAvoidance}
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

### Change 2: Pass `positionerClassName="z-[65]"` from `LocateSongsetsPopover`

**File:** `delivery/webapp/src/components/audio/LocateSongsetsPopover.tsx`

Add `positionerClassName="z-[65]"` to the existing `<PopoverContent>` call (which already has `side="top"` and `collisionAvoidance`):

```tsx
<PopoverContent
  className="w-72 p-0 min-h-[240px]"
  align="start"
  side="top"
  collisionAvoidance={{ side: "none", fallbackAxisSide: "none" }}
  positionerClassName="z-[65]"
>
```

`z-[65]` sits above the player bar (`z-[60]`) and below the fullscreen controller (`z-[70]`).

### Change 3: Add min-height floor to `LocateSongsetsPopover` popup content

**File:** `delivery/webapp/src/components/audio/LocateSongsetsPopover.tsx`

Add `min-h-[240px]` to the `className` prop of `<PopoverContent>` (shown in the Change 2 code block above). This `className` is forwarded to the `Popup` element via `cn(...)`, not the positioner. The popup renders at least 240px tall (≈4 rows + search affordance). When `--available-height` shrinks below this (lyrics expanded, short viewport, mobile landscape), the popup overflows the top of the viewport rather than rendering as a 2-row sliver — a clear visual signal that something is wrong.

Add a comment above the `collisionAvoidance` line in `LocateSongsetsPopover.tsx`:

```tsx
{/* Disable flip intentionally to avoid v3's side="right" overlap with the player bar. min-h-[240px] on the Popup is the mitigation for short viewports. */}
```

### Change 4: Drop prop-spying test; move assertions to `popover.test.tsx`

**File:** `delivery/webapp/src/test/components/audio/AudioPlayerBar.test.tsx`

- Delete the `vi.mock("@/components/ui/popover", ...)` block (lines 16–22).
- Delete the `import { PopoverContent } from "@/components/ui/popover"` import (line 6).
- Delete the two spy-based tests: "passes side='top' to PopoverContent" (lines 847–872) and "passes collisionAvoidance with side='none'..." (lines 874–901).
- Keep all other tests (render, focus, lyrics toggle, keyboard shortcuts, list container, ARIA roles, 15 songset names).
- Add a new test in the `LocateSongsetsPopover` describe block: "popover content has min-h-[240px] class" — open the popover, query `[data-slot='popover-content']`, assert its className contains `min-h-[240px]`. This is a DOM-level assertion, not a spy.

**File:** `delivery/webapp/src/test/components/ui/popover.test.tsx`

- Update existing test: rename "positioner className contains z-[70] (not z-50)" → "default positioner className is z-50". Assert the positioner has `z-50` class and does NOT contain `z-[70]`.
- Add test: "positionerClassName override is forwarded" — render `<PopoverContent positionerClassName="z-[65]">`, open popover, assert positioner className contains `z-[65]` and does NOT contain `z-50`.
- Add test: "collisionAvoidance is forwarded to Positioner" — render `<PopoverContent side="top" collisionAvoidance={{ side: "none", fallbackAxisSide: "none" }}>`, open popover, assert `[data-slot='popover-content']` has `data-side="top"` and the positioner element exists.

### Change 5: Add z-index regression guard test

**File:** `delivery/webapp/src/test/components/ui/popover.test.tsx`

Add a test: "stacking-order classname guard (jsdom does not compute stacking; this is a classname regression guard, not a visual stacking test)" — render `PopoverContent` with default props, open popover, assert the positioner has `z-50` class. This catches future PRs that try to bump the global default again.

### Change 6: Add WebKit/Safari runbook + process guard

**File:** `delivery/webapp/AGENTS.md`

Add under the Architecture section:

> When modifying `components/ui/popover.tsx`, you must perform manual DevTools verification at desktop (1280×776) and mobile (375×667) widths, plus Safari iOS Simulator. Paste screenshots in the PR description.

This spec (v5) owns the canonical manual runbook going forward (see Testing Plan below).

## Files to Modify

| File | Changes |
|------|---------|
| `delivery/webapp/src/components/ui/popover.tsx` | Revert `z-[70]` → `z-50` as default. Add `positionerClassName?: string` prop (default `"z-50"`), apply to `Positioner` className. |
| `delivery/webapp/src/components/audio/LocateSongsetsPopover.tsx` | Pass `positionerClassName="z-[65]"`. Add `min-h-[240px]` to `className`. Add comment explaining flip-disable tradeoff. |
| `delivery/webapp/src/test/components/ui/popover.test.tsx` | Update default-z test from `z-[70]` to `z-50`. Add `positionerClassName` override test. Add `collisionAvoidance` forwarding test. Add stacking-order classname guard. |
| `delivery/webapp/src/test/components/audio/AudioPlayerBar.test.tsx` | Delete `vi.mock` spy + import. Delete 2 spy-based tests. Add `min-h-[240px]` DOM assertion. Keep all other tests. |
| `delivery/webapp/AGENTS.md` | Add manual-verification requirement note for `popover.tsx` changes. |

## Implementation Order

Changes 1–5 should land in a **single follow-up PR** ("address PR-138 review"). Rationale: the spy-based tests assert `z-[70]`; reverting `z-[70]` without removing the spy would break the tests. Bundling avoids a broken intermediate state.

Order within the PR:

1. Change 1 — revert z + add `positionerClassName`
2. Change 2 — pass `z-[65]` from `LocateSongsetsPopover`
3. Change 3 — `min-h-[240px]` + comment
4. Change 4 — test refactor (drop spy, move to `popover.test.tsx`)
5. Change 5 — regression guard test
6. Change 6 — `AGENTS.md` note (runbook is this spec itself)

## Testing Plan

### Automated tests

```bash
cd delivery/webapp && pnpm test -- --run
cd delivery/webapp && pnpm lint && pnpm typecheck
```

**`popover.test.tsx`** covers:
- Default positioner className is `z-50` (not `z-[70]`).
- `positionerClassName="z-[65]"` is forwarded and wins over default.
- `side="top"` produces `data-side="top"` on the popup.
- `collisionAvoidance` is forwarded (positioner exists, popup renders).
- Stacking-order classname guard (default still `z-50`).

**`AudioPlayerBar.test.tsx`** covers (in the `LocateSongsetsPopover` describe block):
- Min-height floor (`min-h-[240px]`) on the popup content.
- All existing behavior tests retained (render, focus return, list container, ARIA roles, 15 songset names).

### Manual / DevTools verification

This is the canonical runbook (supersedes v3's Testing Plan):

1. **Desktop Chrome 1280×776, 1 / 12 / 15 / 20 songsets** — all rows visible, no clipping. Popup `data-side="top"`, bottom anchored above player bar.
2. **Desktop Chrome, lyrics panel expanded (`L` key)** — popover must not collapse below ~5 rows. `--available-height` reduced; popup scrolls internally.
3. **Mobile Chrome 375×667** — popover must show ≥5 rows or scroll comfortably.
4. **Safari iOS 17+ Simulator, 375×667, player bar visible** — popup must render above the player bar; scrolling within the popup must not scroll the underlying page. `AudioPlayerBar` uses `backdrop-blur` which can promote to a stacking context on Safari — verify no regression.
5. **Landscape 667×375** — popover must not collapse to a sliver (`min-h-[240px]` active; may overflow top of viewport, which is the intended safe failure mode).
6. **Z-index check:** inspect positioner element. Computed `z-index` is 65 (above player `z-[60]`, below controller `z-[70]`).
7. **Other overlays unaffected:** confirm `Dialog`, `Sheet`, `Select`, `DropdownMenu`, `Tooltip` still render at `z-50`.

## Risks & Considerations

- **Visual overlap is intentional.** With `z-[65]`, the popover's bottom rows render on top of the player bar in the overlap zone. This matches v3's `side="top"` design — the popup bottom anchors at `trigger.top - sideOffset`, so overlap is minimal. The z-index ensures any residual overlap paints the popover above the player. This is the same pattern used by menus/selects that overlay surrounding UI.
- **`z-[65]` election.** Chosen to sit strictly above the player bar (`z-[60]`) and strictly below the fullscreen controller (`z-[70]`). Both v3 and v4 proposed `z-[70]`, which collides with `ControllerPlayer`. `z-[65]` resolves that collision.
- **Min-height floor tradeoff.** `min-h-[240px]` means the popup overflows the top of the viewport in extreme cases (very short landscape viewports), rather than rendering as a 2-row sliver. Overflow is the safer failure mode — it signals "something is wrong" rather than silently degrading.
- **WebKit/Safari.** `AudioPlayerBar` uses `backdrop-blur` which can promote it to its own stacking context on Safari. Manual Safari verification is required (see Testing Plan row 4).
- **Scoped vs global.** This spec adopts the scoped `positionerClassName` approach from v4. The shared `PopoverContent` default returns to `z-50`, restoring parity with all other overlay primitives. Only `LocateSongsetsPopover` overrides to `z-[65]`.

## Out of Scope

- `positionerProps` escape hatch (was considered, dropped — only one consumer, `collisionAvoidance` already typed via `Pick<>`).
- Tailwind theme-driven z-scale (`z-popover-player`, `z-controller`, etc.) — keep numeric for now; refactor if a third overlay tier emerges.
- Re-implementing `flip` with a custom `fallbackPlacements` list — revisit only if min-height floor proves worse UX than constrained flip.
- Modifying `AudioPlayerBar`'s `z-[60]` to use `dvh` units.
- `--available-height` caching behavior in Base UI.

## Notes

- The `side` and `collisionAvoidance` forwarding (PR #138's Change 1) is retained as-is — it was a correct fix for a real bug.
- The `side="top"` + `collisionAvoidance={{ side: "none", fallbackAxisSide: "none" }}` (PR #138's Change 3) is retained as-is — correct for avoiding the flip-to-right overlap.
- Base UI `collisionAvoidance` prop: type `SideFlipMode` with `side: 'flip' | 'none'`, `align: 'flip' | 'shift' | 'none'`, `fallbackAxisSide: 'start' | 'end' | 'none'`. Default `side: 'flip'`, `align: 'flip'`, `fallbackAxisSide: 'end'`.
- Setting `collisionAvoidance.side = 'none'` disables the `flip` middleware. The `size` middleware still publishes `--available-height`, so `max-h-(--available-height)` still caps the popup.

## Related Specs

- `specs/fix-webapp-player-locate-songsets-popover-clipping.md` (v1)
- `specs/fix-webapp-player-locate-songsets-popover-clipping-v2.md` (v2, shipped as commit `c433baa4`)
- `specs/fix-webapp-player-locate-songsets-popover-clipping-v3.md` (v3, PR #138 implemented this — superseded by v5)
- `specs/fix-webapp-player-locate-songsets-popover-clipping-v4.md` (v4, proposed scoped approach — superseded by v5, which adopts the scoped approach with `z-[65]`)
- `specs/pr-138-popover-clipping-review-fixes.md` (review of PR #138, fed into this v5 spec)
