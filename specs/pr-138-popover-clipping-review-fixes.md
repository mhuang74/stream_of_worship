# PR #138 Review — LocateSongsetsPopover Clipping (v3): Top 5 Critical / High-Priority Items

## Context

PR #138 (head `8870cfaf`, branch `fix_songset_containing_search`) ships the "v3" implementation of the popover clipping fix described in `specs/fix-webapp-player-locate-songsets-popover-clipping-v3.md`. It makes four additive changes:

1. `popover.tsx` now destructures `side` and `collisionAvoidance` and forwards them to `PopoverPrimitive.Positioner` (fixing a real bug where `side` was silently dropped onto `Popup` and ignored).
2. `popover.tsx` hard-codes the positioner className from `z-50` to `z-[70]` — globally, for every consumer.
3. `LocateSongsetsPopover.tsx` pins `side="top"` and disables flip via `collisionAvoidance={{ side: "none", fallbackAxisSide: "none" }}`.
4. New / updated tests assert prop forwarding (`side="top"`, `collisionAvoidance`, `z-[70]` in positioner className) and render 15 mock songsets.

The prop-forwarding fix (#1) and the `side="top"` + anti-flip choice for *this specific popover* (#3) are correct and well-grounded in the v3 spec's DevTools measurements. The concerns below are UX/operational, not "the fix is wrong."

This document is **plan-only**: it identifies the top 5 issues with PR #138 and describes the implementation plan to address them. **Do not implement.** Implementation should happen in a follow-up PR after this plan is reviewed.

Related specs (read first, do not duplicate):
- `specs/fix-webapp-player-locate-songsets-popover-clipping.md` (v1)
- `specs/fix-webapp-player-locate-songsets-popover-clipping-v2.md` (v2, shipped as commit `c433baa4`)
- `specs/fix-webapp-player-locate-songsets-popover-clipping-v3.md` (v3, the spec PR #138 claims to implement)
- `specs/fix-webapp-player-locate-songsets-popover-clipping-v4.md` (v4, *rejected* alternative — scoped `positionerClassName`)

The v4 spec was already an explicit design decision to **scope** the z-index override rather than change the shared default. PR #138 reimplements v3's global override instead, without addressing why v4 was rejected. That tension is the root of the top finding below.

## Top 5 Critical / High-Priority Items

### 1. CRITICAL — Global z-index bump from `z-50` to `z-[70]` collides with `ControllerPlayer` and inverts the modal layering contract

**Severity:** Critical (UX + runtime).
**Files affected:** `delivery/webapp/src/components/ui/popover.tsx:47` (PR change), `delivery/webapp/src/components/play/ControllerPlayer.tsx:864`.

**Problem.**

The PR changes the positioner className of the shared `PopoverContent` from `z-50` to `z-[70]`. Two consequences:

- **Same tier as the cast / fullscreen controller.** `ControllerPlayer.tsx:864` is `fixed inset-0 z-[70]`. The v3 spec explicitly noted (`specs/...v3.md` Notes section): *"verify no conflict; if needed use `z-[65]` or `z-[80]`"*. PR #138 did not resolve this — it landed `z-[70]` anyway. When two elements share a stacking level, paint order is determined by DOM order, which is undefined behavior from a design standpoint and depends on Portal mount order. If `LocateSongsetsPopover` is opened while a projection/controller session is active — or while the player bar is mounted inside a route where `ControllerPlayer` is also rendered — we get inconsistent overlap between two `z-[70]` layers.
- **Inverts the modal contract.** Every other overlay primitive in the codebase is `z-50`:
  - `components/ui/dialog.tsx:34` (overlay `z-50`), `:56` (content `z-50`)
  - `components/ui/alert-dialog.tsx:33,55`
  - `components/ui/sheet.tsx:31,56`
  - `components/ui/dropdown-menu.tsx:46,54`
  - `components/ui/select.tsx:81,86`
  - `components/ui/tooltip.tsx:53,58`
  - `components/offline/OfflineIndicator.tsx:37`

  Post-PR, if any popover is open when a `Dialog` / `Sheet` / `DropdownMenu` / `Select` is triggered, the `z-[70]` popover paints **above** the modal and its backdrop, breaking the visual "modal captures focus" contract. Today this is mostly theoretical because `LocateSongsetsPopover` is currently the only `PopoverContent` consumer — but `PopoverContent` is exported as a shared primitive (`components/ui/popover.tsx`) and any future consumer inherits the bug.

**Plan.**

(a) **Revert the global default back to `z-50`.** Keep `PopoverContent`'s positioner at `z-50`. This restores parity with the other overlay primitives and removes the contract inversion.

(b) **Introduce a scoped override, then use `z-[65]` (between the player `z-[60]` and controller `z-[70]`).** Adopt the v4 planned API: add an optional `positionerClassName?: string` prop on `PopoverContent` (default `"z-50"`) that is spread onto `PopoverPrimitive.Positioner`'s `className`. Pass `positionerClassName="z-[65]"` from `LocateSongsetsPopover` only.

(c) **Document the z-index scale.** Add a top-of-file comment in `components/ui/popover.tsx` listing the tiers: dropdowns/tooltips/selects/dialogs `z-50`, audio player `z-[60]`, player-bar popovers `z-[65]`, fullscreen controller `z-[70]`. This is the single source of truth for future stacking decisions.

(d) **Tests.** Update `src/test/components/ui/popover.test.tsx` so the default-render case asserts `"z-50"` in the positioner className, and a second case asserts `positionerClassName="z-[65]"` is forwarded and wins. Update `AudioPlayerBar.test.tsx` popover assertions so the prop-spied `PopoverContent` mock expects `positionerClassName: "z-[65]"` (instead of asserting z-[70] on the shared primitive).

**Acceptance.**

- `components/ui/popover.tsx` does not contain `z-[70]`.
- `ControllerPlayer.tsx` remains the only `z-[70]` in `src/`.
- `components/audio/LocateSongsetsPopover.tsx` passes `positionerClassName="z-[65]"` and the positioner renders with that class.
- Dialog/Sheet/DropdownMenu/Select z-indices are unchanged.

---

### 2. HIGH — `collisionAvoidance={{ side: "none", fallbackAxisSide: "none" }}` silently degrades short-viewport / lyrics-open UX instead of failing safely

**Severity:** High (UX ergonomics).
**Files affected:** `delivery/webapp/src/components/audio/LocateSongsetsPopover.tsx:114`.

**Problem.**

The PR hard-disables Floating UI flip and perpendicular-axis fallback. In Base UI 1.x (`node_modules/@base-ui/react/utils/useAnchorPositioning.js:154`), `collisionAvoidanceSide === 'none'` sets `flipMiddleware = null`. The `size` middleware (`useAnchorPositioning.js:206`) still publishes `--available-height` correctly when flip is disabled, so `max-h-(--available-height)` still caps the popup.

However, this means: when the available space above the trigger collapses — lyrics panel expanded (`AudioPlayerBar` shows a `max-h-[40dvh]` lyrics area, see `handleToggleLyrics`), very short viewport (mobile landscape `h < 480`), or non-`bottom` safe-area-inset cases — the popup is force-anchored at `side="top"` with whatever height is left. Worst case: `--available-height` shrinks to <100px and the user sees a 2–3-row-tall scrollable sliver with no explanatory UI.

The v3 spec assumes "popup bottom anchors at `trigger.top - sideOffset` (~686px), well within the viewport" based on a desktop viewport with default lyrics-off state. It does not test:

- Lyrics panel expanded (raises the trigger anchor point, reduces `--available-height`).
- 320×568 / 375×667 mobile viewports.
- 480px-tall landscape viewports.

The non-flip behavior was chosen specifically to avoid the `side="right"` overlap with the player bar, but a bounce-back rule that prefers `bottom` (where there is no space, since the trigger is in the bottom-fixed bar) and then gracefully degrades would have been safer than disabling flip outright.

**Plan.**

(a) **Add a min-height floor.** In `LocateSongsetsPopover`, apply `min-h-[240px]` (≥4 rows + search affordance) to the popup content. If `--available-height` is less than this, the popup will overflow the top of the viewport rather than render as a 2-row sliver — and the user gets a clear visual signal that something is wrong, which is better than a silently-broken UI.

(b) **Add a viewport-height smoke assertion.** Extend `AudioPlayerBar.test.tsx` with one test that: (i) sets `window.innerHeight = 480`, (ii) opens the popover, (iii) asserts the `data-side="top"` attribute is still emitted (no flip occurred), (iv) asserts the content has the `min-h-[240px]` class. We cannot measure real layout in jsdom, so we assert props/classnames as tripwires.

(c) **Add a Playwright / DevTools checklist step.** Update the v3 spec's Testing Plan with two new rows:
- "Lyrics panel expanded (`L` key) at 1280×776: popover must not collapse below ~5 rows."
- "Mobile viewport 375×667: popover must show ≥5 rows or scroll comfortably."

(d) **Document the tradeoff.** Add a one-line comment above the `collisionAvoidance={{ side: "none", ... }}` line in `LocateSongsetsPopover.tsx` explaining that this is intentional to avoid the v3 flip-to-right overlap, and that min-height floor (a) is the mitigation.

**Acceptance.**

- With lyrics expanded and at 480p viewport, the popover renders min 240px tall and does not flip to `side="right"`.
- Tests pass: `pnpm test -- --run AudioPlayerBar popover`.
- Manual runbook in v3 spec is extended for the two new viewport cases.

---

### 3. HIGH — Prop forwarding opened a long-term footgun: `Positioner.Props` now leak through `Popup`-typed call sites, with no compile-time signal when the Base UI shape diverges

**Severity:** High (operational / maintainability).
**Files affected:** `delivery/webapp/src/components/ui/popover.tsx:28–58`.

**Problem.**

The PR widens the type of `PopoverContent` from `Popup.Props & Pick<Positioner.Props, "align"|"sideOffset"|"side">` to also include `"collisionAvoidance"`. Functionally this is correct, but the type intersection with `Popup.Props` means callers can pass *any* `Popup.Props` (subtitle props, focus props, event handlers, `data-*`, etc.) **and** any of the picked `Positioner.Props`. We now have a *structural overlap risk*:

- Any future `Positioner.Props` key we want to expose (e.g. `collisionPadding`, `sticky`, `arrowPadding`) requires manually widening the `Pick<>`. If forgotten, TypeScript still compiles because `...rest` swallows them, then they're spread onto `Popup` and silently ignored — the *exact bug this PR fixed for `side`*.
- The `...props` catch-all still flows onto `PopoverPrimitive.Popup`. `Popup.Props` and `Positioner.Props` share some names (e.g. `style`, `className`, `id`) which makes regressions hard to spot in review.

**Plan.**

(a) **Split into two explicit prop bags.** Refactor `PopoverContent` to accept a single `positionerProps?: Partial<Positioner.Props>` alongside the existing `Popup.Props` spread:

```tsx
function PopoverContent({
  className,
  align = "center",
  side = "bottom",          // keep Base UI default
  sideOffset = 4,
  collisionAvoidance,
  positionerClassName,      // added by Item 1 plan
  positionerProps,          // NEW — escape hatch for any Positioner-only prop
  ...props                   // Popup props
}: PopoverPrimitive.Popup.Props & Pick<...> & { positionerClassName?: string; positionerProps?: Partial<PopoverPrimitive.Positioner.Props> }) {
  return (
    <PopoverPrimitive.Portal>
      <PopoverPrimitive.Positioner
        align={align}
        side={side}
        sideOffset={sideOffset}
        collisionAvoidance={collisionAvoidance}
        className={positionerClassName ?? "z-50"}
        {...positionerProps}  // escape hatch
      >
        <PopoverPrimitive.Popup ... />
      </PopoverPrimitive.Positioner
    </PopoverPrimitive.Portal>
  );
}
```

This eliminates the silent-drop hazard class (any `Positioner`-only prop has a typed channel) and removes the temptation to keep widening the `Pick<>` over time.

(b) **Add a type-level test.** In `popover.test.tsx` use `expectTypeOf` (already available via Vitest) to assert that `positionerProps` accepts `Positioner.Props` keys. This is a compile-time tripwire for future Base UI upgrades.

(c) **Update `AGENTS.md`** under `delivery/webapp/AGENTS.md` — add a one-line note: "When extending `PopoverContent`, prefer `positionerProps` for `Positioner`-specific keys; do not widen the `Pick<>`." This preserves the team knowledge going forward.

**Acceptance.**

- `PopoverContent` exposes `positionerProps`.
- All existing call sites (`LocateSongsetsPopover` only) and tests compile with no changes required to their public API.
- `pnpm typecheck` passes.

---

### 4. HIGH — Prop-spying test on `PopoverContent` makes `AudioPlayerBar.test.tsx` fragile against internal refactors of the shared primitive

**Severity:** High (operational / test hygiene).
**Files affected:** `delivery/webapp/src/test/components/audio/AudioPlayerBar.test.tsx:15–22, 847–end`.

**Problem.**

The PR adds:

```tsx
vi.mock("@/components/ui/popover", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/ui/popover")>();
  return { ...actual, PopoverContent: vi.fn(actual.PopoverContent) };
});
```

This pattern replaces the real `PopoverContent` with a spy that wraps it. Three problems:

- **Brittleness.** Any future change to `PopoverContent`'s call signature (including the Item 3 `positionerProps` refactor) requires updating every assertion in this file that reads `vi.mocked(PopoverContent).mock.calls[i][0]`. The test breaks not because behavior changed but because the internal shape did.
- **Spying loses referential equality.** `vi.fn(actual.PopoverContent)` returns a new function each test run; Base UI's portal rendering + React fiber reconciliation can occasionally remount the popover mid-test, causing `mock.calls.length > 1`. The current tests already paper over this by taking `calls[calls.length - 1]`, but that implicit assumption isn't documented.
- **Asymmetric coverage.** The mock is installed for the entire `AudioPlayerBar.test.tsx` module — every other test that touches `PopoverContent` (now or in the future) faces the spy being installed whether it cares or not.

**Plan.**

(a) **Move the prop-forwarding assertions into `popover.test.tsx`.** The contract being tested is "LocateSongsetsPopover passes `side="top"` and `collisionAvoidance={...}` to `PopoverContent`." This is really two contracts:

   1. *`PopoverContent` honors the props it's given* — testable directly in `popover.test.tsx` by rendering `PopoverContent side="top" collisionAvoidance={...}` and asserting `data-side="top"` and inspecting the positioner's className / element.
   2. *`LocateSongsetsPopover` passes the right props* — testable without a spy by asserting the rendered DOM (e.g. `data-side="top"` on the `[data-slot='popover-content']` element, positioner has the overridable z-class from Item 1).

(b) **Drop the spy from `AudioPlayerBar.test.tsx`.** Once (a) covers both halves, delete `vi.mock("@/components/ui/popover", ...)` and the assertions that read `vi.mocked(PopoverContent).mock.calls`. The remaining tests (render count, focus return, listitem visibility) continue to use the real primitive.

(c) **Retire `calls.length - 1` pattern.** This is gone with the spy; no replacement needed.

**Acceptance.**

- `AudioPlayerBar.test.tsx` no longer contains `vi.mock("@/components/ui/popover", ...)`.
- `popover.test.tsx` directly covers `side` and `collisionAvoidance` forwarding, plus the scoped `positionerClassName` (Item 1).
- Total test count remains ≥ current; runtime should marginally improve (less per-test render overhead from the spy wrapper).

---

### 5. HIGH — No regression guard for other overlay primitives and no cross-browser / WebKit verification step

**Severity:** High (operational risk).
**Files affected:** Process / testing, not source code.

**Problem.**

The PR's "Blast Radius" section claims "`PopoverContent` has one consumer in the entire codebase ... the blast radius is zero." This is true today, but it does not protect:

- **Future consumers** of `PopoverContent` (any new feature that imports it) instantly inherit the `z-[70]` default and the layering contract change (Item 1).
- **WebKit / iOS Safari** behavior. Base UI's anchor positioning + sticky popovers inside a `fixed` parent has had historical Safari stack-order quirks (compositing changes triggered by `backdrop-blur-sm` on the audio player can promote it to its own stacking context). The PR was verified only against Chrome DevTools.
- **Existing `data-side=right` consumers of other primitives** (e.g. `Select` with `max-h-(--available-height)`) — none are inside a `fixed` bar, but the PR doesn't verify any of them either.

The "Test Plan" checklist shows: `pnpm test`, `pnpm lint`, `pnpm typecheck` all green; **manual DevTools verification is unchecked**.

**Plan.**

(a) **Add an integration smoke test in `popover.test.tsx`** that mounts a `Dialog` and a `Popover` simultaneously and asserts their stacking order via reads of class names — dialog overlay content `z-50` and popover positioner `z-[65]` (post-Item 1). This is a classname-level tripwire that catches future PRs that try to bump the global default again.

(b) **Extend the manual runbook with WebKit checks.** Add a row to the v3 spec's Testing Plan: "Open the popover on Safari iOS 17+ (Simulator is sufficient) at 375×667 with the player bar visible: popup must render above the player bar; scrolling within the popup must not scroll the underlying page."

(c) **Add an unchecked-boxes CI gate for the PR template** (`.github/pull_request_template.md` if present, or `AGENTS.md`): "If your PR modifies `components/ui/popover.tsx`, you must paste a screenshot of manual DevTools verification at desktop and mobile widths." Lightweight process guard.

(d) **Document the z-index scale** (already covered in Item 1(c); cross-reference from this Item).

**Acceptance.**

- `AGENTS.md` or pull request template contains the manual-verification requirement for `popover.tsx` changes.
- `popover.test.tsx` includes a stacking-order smoke test.
- v3 spec's Testing Plan includes WebKit/iOS Safari row.

---

## Implementation Order

These items have dependencies; land in the following order:

1. **Item 1** — Revert global `z-[70]`; introduce `positionerClassName`; use `z-[65]` in `LocateSongsetsPopover`. (Unblocks the rest.)
2. **Item 3** — Add `positionerProps` escape hatch. (Pure additive; low-risk.)
3. **Item 4** — Move prop-spy tests to `popover.test.tsx`; delete the spy from `AudioPlayerBar.test.tsx`.
4. **Item 2** — Add `min-h-[240px]` floor + new short-viewport test cases.
5. **Item 5** — Smoke test + runbook / process guards.

Items 1–3 should land in a single follow-up PR ("address PR-138 review, items 1–3"). Items 4–5 can land in a second follow-up ("hardening"), or together.

## Out of Scope

- Re-implementing `flip` with a custom `fallbackPlacements` list that prefers `top` and never returns `right`. Worth revisiting only if Item 2's min-height floor turns out to be a worse UX than a constrained flip.
- Modifying `AudioPlayerBar`'s `z-[60]` to use CSS `dvh` units for safer layer math.
- Removing `--available-height` caching behavior in Base UI.

## Open Questions for Reviewer

- Is `z-[65]` an acceptable middle tier, or do we want to refactor to a Tailwind theme-driven z-scale (`z-popover-player`, `z-controller`, etc.)? Recommendation: keep `z-[65]` numeric for now, scale-refactor in a separate PR if a third overlay tier emerges.
- Should `positionerProps` be typed as `Partial<Positioner.Props>` or as an explicit allowlist of safe keys (`collisionPadding`, `sticky`, `arrowPadding`)? Recommendation: `Partial<>` for forward-compat — Base UI's `Positioner.Props` is already the authoritative surface.

## Validation Plan

- All existing tests continue to pass: `cd delivery/webapp && pnpm test -- --run`.
- New tests added in Items 1, 2, 4, 5 pass.
- `pnpm lint && pnpm typecheck` clean.
- Manual verification at:
  - Desktop Chrome, 1280×776, 1 / 12 / 15 / 20 songsets.
  - Desktop Chrome, lyrics panel expanded (`L` key).
  - Mobile Safari iOS Simulator, 375×667.
  - Landscape 667×375.
- Browser DevTools "Layers" panel confirms popover layer paints above player bar (`z-[60]`) and below `ControllerPlayer` (`z-[70]`).
