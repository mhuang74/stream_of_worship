# Implementation Plan: Fix Mobile Song Row Metadata Cramping

**Status:** Pending  
**Date:** 2026-05-26  
**Component:** Songset Editor Screen  
**File:** `webapp/src/components/songset/SongList.tsx`

---

## 1. Objective

Fix the cramped song metadata in the Songset Editor on mobile viewports. There are **two contributing issues**:

1. **Primary:** The transition button (`hidden sm:flex`) is never hidden on mobile because `--breakpoint-sm: 0px` makes `sm:` always apply. This consumes ~113-123px, leaving only ~79-89px for song info.
2. **Secondary:** The metadata row (artist, duration, key) uses single-line flex without wrapping, causing overflow and character-by-character text breaking when space is tight.

---

## 2. Root Cause Analysis

### Issue A — Broken Responsive Visibility

In `webapp/src/app/globals.css:9`:

```css
/* Mobile-first breakpoints: sm=0 (phone), md=768px (tablet), lg=1024px (desktop) */
--breakpoint-sm: 0px;
```

This means `hidden sm:flex` in `SongList.tsx:208` resolves to `display: flex` at **all** viewport widths. The transition button is never hidden.

**Impact:**
- Transition button consumes ~113-123px on mobile
- Song info container gets only ~79-89px
- Artist name (e.g., 游智婷) wraps character-by-character vertically

### Issue B — Non-wrapping Metadata

In `SongList.tsx:186`:

```tsx
<div className="flex items-center gap-2 text-xs text-muted-foreground mt-0.5">
```

No `flex-wrap`, so content overflows horizontally on narrow viewports.

---

## 3. Proposed Changes

### Change 1: Fix Transition Button Visibility (Primary Fix)

**File:** `webapp/src/components/songset/SongList.tsx`  
**Line:** ~208

**Before:**
```tsx
className="shrink-0 text-xs text-muted-foreground hidden sm:flex"
```

**After:**
```tsx
className="shrink-0 text-xs text-muted-foreground hidden md:flex"
```

**Rationale:** Since `sm:` is redefined as `0px` (always-on), we use `md:` (768px) to properly hide the button on mobile and show it on tablet/desktop.

> **Note:** We do NOT change `globals.css` because `--breakpoint-sm: 0px` is an intentional mobile-first design choice used throughout the app. Changing it would break many other components.

### Change 2: Allow Metadata to Wrap (Secondary Fix)

**File:** `webapp/src/components/songset/SongList.tsx`  
**Line:** ~186

**Before:**
```tsx
<div className="flex items-center gap-2 text-xs text-muted-foreground mt-0.5">
```

**After:**
```tsx
<div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted-foreground mt-0.5">
```

**Rationale:** Even with the button hidden, very long artist names or narrow viewports may still need wrapping. `flex-wrap` with `gap-x-2 gap-y-0.5` ensures graceful fallback without excessive vertical spacing.

---

## 4. Expected Behavior

| Viewport | Transition Button | Metadata Layout |
|----------|-------------------|-----------------|
| **Mobile (≤767px)** | Hidden | Wraps if needed (artist on line 1, duration/key on line 2) |
| **Tablet/Desktop (≥768px)** | Visible | Single line |

---

## 5. Verification Steps

1. **Visual check:** Use Chrome DevTools (iPhone SE / 375px) to confirm:
   - Transition button is hidden
   - Artist name no longer breaks character-by-character
   - Metadata wraps gracefully if needed

2. **Regression test:** Run SongList unit tests:
   ```bash
   pnpm --filter sow-webapp test -- src/test/components/songset/SongList.test.tsx
   ```

3. **Cross-viewport check:** Verify button appears at ≥768px and remains hidden below.

---

## 6. Rollback Plan

If `md:flex` causes the button to appear too late (e.g., on small tablets), consider:
- `hidden [@media(min-width:640px)]:flex` — custom breakpoint
- Or revert to `hidden sm:flex` if the breakpoint definition is fixed globally

---

## 7. Files Modified

| File | Change |
|------|--------|
| `webapp/src/components/songset/SongList.tsx` | Line ~186: Add `flex-wrap` to metadata container |
| `webapp/src/components/songset/SongList.tsx` | Line ~208: Change `sm:flex` to `md:flex` |

---

## 8. Related Context

- Tailwind breakpoint config: `webapp/src/app/globals.css:7-14`
- Mobile-first design intent: `sm=0px` means "phone and up" (always-on)
