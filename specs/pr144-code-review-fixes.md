# PR #144 Code Review Fixes

## Status

Planning. **Do not implement** until this spec is reviewed.

## Summary

Address findings from the two-axis code review of PR #144 (branch `sow_chinese_menus`, "feat(webapp): bilingual UI") against issue #143.

One review item was **verified as a false positive and dropped**: the Standards "Speculative Generality — unused `setLocale`". `src/app/settings/page.tsx:33` destructures `setLocale` and `settings/page.tsx:83` (`handleSave`) calls `setLocale(updated.locale)`, so the locale switches immediately on save. The client-side state and its `setLocale` are live, not dead.

| # | Severity | Issue | Component |
|---|----------|-------|-----------|
| 1 | High | Share token pages are no-auth, so `useLocale()` never resolves `zh-Hant` — the share/controller/projection chrome stays English for a Chinese user (Stories #4, #13 unmet) | `lib/i18n/server.ts`, `app/api/settings/route.ts` |
| 2 | High | `mergeMessages` silently overwrites duplicate keys across bundles — weaker than the spec's "compile/test failure, not a runtime blank" guarantee | `lib/i18n/messages.ts` |
| 3 | Medium | `formatTotalDuration` duplicated between share landing and `ShareDialog` with a hardcoded separator and inconsistent singular/plural keys | `app/share/[token]/page.tsx`, `components/share/ShareDialog.tsx` |
| 4 | Medium | Option-label key helpers copy-pasted verbatim between `SettingsForm` and `RenderForm` | `components/settings/SettingsForm.tsx`, `components/render/RenderForm.tsx` |
| 5 | Low | Locale error string hardcodes the allowed set instead of reusing the `LOCALES` const | `app/api/settings/route.ts` |
| 6 | Low | Home page reverted progress: `page.tsx` was made `"use client"` to call `useLocale()`, degrading SSR for a shell page | `app/page.tsx` |
| 7 | Low (scope) | Unrelated docs commit (ADR 0003 five-phase arc + CONTEXT.md glossary) rides in this PR | `docs/adr/0003-*`, `CONTEXT.md` |

Accepted, no change (documented tradeoffs, not bugs):

- **Shotgun Surgery** — ~80 components each add `const { t } = useLocale()` and thread `t` through effect deps. Inherent to the Provider+hook design that ADR 0004 mandates; a module-scope `t()` would break React reactivity. No change.
- **Login/register hardcoded English** — spec Story #14 explicitly keeps pre-login pages English. No change.
- **`popover.tsx` manual-verification rule** — the file is untouched by this diff; rule does not apply.

---

## Issue 1 — Share token pages default to English for a zh-Hant user

### Severity: High

### Files

- `delivery/webapp/src/lib/i18n/server.ts`
- `delivery/webapp/src/app/api/settings/route.ts` (PUT handler)

### Problem

`resolveUserLocale` (`lib/i18n/server.ts`) returns `"en"` for unauthenticated requests:

```ts
const session = await auth.api.getSession({ headers });
if (!session?.user) return "en";
```

Share token pages (`/share/[token]/...`, including `controller` and `projection`) are public/no-auth. They all wire `useLocale()` correctly, but the `LocaleProvider` is seeded with `en` (SSR returns `en`, `<html lang>` is `en`), so a zh-Hant user following a shared link always sees English chrome. This leaves issue #143 Stories #4 ("navigation... in Traditional Chinese") and #13 ("the browser page language attribute to reflect my chosen language") unsatisfied for the share/controller/projection surfaces, which #143 explicitly lists under "Surface converted".

### Fix

Persist the chosen locale to a browser cookie so public pages can resolve it. The cookie is an explicit user choice (not `Accept-Language` detection), so it stays consistent with ADR 0004's "resolved in-app, not a URL route".

#### Step A — Set the cookie server-side on locale save

In the `PUT /api/settings` handler, after the upsert succeeds, set `Set-Cookie` on the response. The handler at `route.ts:222` already computes `locale: isLocale(b.locale) ? b.locale : DEFAULTS.locale` into a `values` object and upserts it. Replace the bare `return NextResponse.json({ settings: values });` at line 234 with:

```ts
const response = NextResponse.json({ settings: values });
response.cookies.set("sow_locale", values.locale, {
  path: "/",
  maxAge: 60 * 60 * 24 * 365,
  sameSite: "lax",
  secure: process.env.NODE_ENV === "production",
});
return response;
```

Setting it server-side (rather than via `document.cookie`) makes the cookie present on the very next request, including SSR of any public page.

#### Step B — Read the cookie in `resolveUserLocale` for unauthenticated / error paths

```ts
import { cookies, headers } from "next/headers";
import { isLocale, Locale } from "./messages";
import { auth } from "@/lib/auth";
import { db } from "@/db";
import { userSettings } from "@/db/schema";
import { eq } from "drizzle-orm";

export async function resolveUserLocale(): Promise<Locale> {
  const cookieLocale = (await cookies()).get("sow_locale")?.value;
  const fallback = (): Locale => (isLocale(cookieLocale) ? cookieLocale : "en");
  try {
    const session = await auth.api.getSession({ headers: await headers() });
    if (!session?.user) return fallback();   // public pages: cookie drives locale
    // authenticated: the account setting is authoritative
    const userId = Number(session.user.id);
    const rows = await db.select({ locale: userSettings.locale }).from(userSettings).where(eq(userSettings.userId, userId));
    const value = rows[0]?.locale;
    return isLocale(value) ? value : fallback();
  } catch {
    return fallback();
  }
}
```

The authenticated branch keeps the account setting authoritative; the cookie only fills the no-auth gap. This makes `<html lang>` and the seeded `LocaleProvider` correct on every share page.

### Verify

- `GET /api/settings` PUT with `locale: "zh-Hant"` returns a `Set-Cookie: sow_locale=zh-Hant` header.
- Visit `/share/<token>/...` while logged out on a browser that has `sow_locale=zh-Hant`: chrome renders zh-Hant, `<html lang="zh-Hant">`.
- Logged-in flow unchanged: an authenticated user's saved setting still wins.
- No regression to pre-login English when the cookie is absent.

---

## Issue 2 — `mergeMessages` silently overwrites duplicate cross-bundle keys

### Severity: High

### Files

- `delivery/webapp/src/lib/i18n/messages.ts` (`mergeMessages`)

### Problem

```ts
const en = Object.assign({}, ...bundles.map((b) => b.en)) as Record<K, string>;
```

If two bundles accidentally define the same key, the later bundle silently overwrites the earlier. The exhaustiveness test passes because both locales collide identically. Today there are 818 keys across 8 bundles with no collision, but the spec's guarantee — "a missing key in either locale is a compile/test failure, not a runtime blank" — is weaker than claimed: a cross-namespace duplicate is a runtime silent overwrite.

### Fix

Detect and fail on cross-bundle collisions at merge time (runs at module load, i.e. build/startup):

```ts
export function mergeMessages<const B extends readonly MessageBundle<string>[]>(
  ...bundles: B
): MessageBundle<BundleKeys<B>> {
  type K = BundleKeys<B>;
  const seen = new Set<string>();
  for (const bundle of bundles) {
    for (const key of Object.keys(bundle.en)) {
      if (seen.has(key)) {
        throw new Error(`i18n: duplicate message key "${key}" across bundles`);
      }
      seen.add(key);
    }
  }
  const en = Object.assign({}, ...bundles.map((b) => b.en)) as Record<K, string>;
  const zhHant = Object.assign({}, ...bundles.map((b) => b["zh-Hant"])) as Record<K, string>;
  return { en, "zh-Hant": zhHant };
}
```

### Verify

- `pnpm test` passes (no current collisions).
- Add a temporary duplicate key to one bundle: `pnpm build` (or a unit import of `messages.ts`) throws "duplicate message key".
- Remove the temp key afterwards.

---

## Issue 3 — `formatTotalDuration` duplication and inconsistent units

### Severity: Medium

### Files

- `delivery/webapp/src/app/share/[token]/page.tsx:70-77` (`formatTotalDuration`)
- `delivery/webapp/src/components/share/ShareDialog.tsx:82-89` (`formatShareDuration`, verbatim duplicate)

Note: the spec previously listed `share/[token]/play/controller/page.tsx`, `play/projection/page.tsx`, `play/audio/page.tsx` as duplicate sites — verified wrong. Those three pages handle playback/projection/audio loading and contain no total-duration helper. The duplication is exactly two sites: `share/[token]/page.tsx` and `ShareDialog.tsx`.

### Problem

`share/[token]/page.tsx` composes a duration with a hardcoded space and mixed singular/plural keys:

```ts
if (totalMinutes < 60) return `${totalMinutes} ${t("control.min")}`;
return `${hours}${t("control.hours")} ${String(mins).padStart(2, "0")}${t("control.mins")}`;
```

`ShareDialog.tsx:82-89` is byte-identical except the `notAvailable` fallback key. The separator space and the `<60` vs `>=60` unit singular/plural (`control.min` vs `control.mins`) differ from `PrePlayCard.tsx:84-92`, which formats the same total via `preplay.hourShort`/`preplay.minShort`. Three surfaces, mutually inconsistent units.

### Fix

Extract one shared `formatTotalDuration(t: (key: TranslationKey) => string, totalSeconds: number | null): string` helper in `delivery/webapp/src/components/play/formatDuration.ts` (or `lib/i18n/format.ts`), and call it from both `share/[token]/page.tsx` and `ShareDialog.tsx`. **Name it distinctly from the existing `lib/format.ts` `formatDuration(seconds)` (non-i18n, `m:ss` style) to avoid shadowing/collision.**

Both required keys already exist in both locales: `control.min` (`分钟`/`分`), `control.hours` (`h`/`小时`), `control.mins` (`m`/`分`), `control.notApplicable`, `control.total`. No new keys needed. Standardize `ShareDialog`'s `control.notAvailable` fallback to the same key the helper uses. Optionally align `PrePlayCard`'s format to the same helper + keys in a follow-up (its `preplay.hourShort`/`preplay.minShort` set is out of this issue's core scope).

### Verify

- Every share page renders `X minutes` / `Xh YYm` identically.
- Under `zh-Hant`, the unit labels swap but remain structurally consistent.
- `pnpm test` covers the shared helper.

---

## Issue 4 — Option-label key helpers duplicated across forms

### Severity: Medium

### Files

- `delivery/webapp/src/components/settings/SettingsForm.tsx:77-87`
- `delivery/webapp/src/components/render/RenderForm.tsx:116-134`

### Problem

`templateLabelKey`, `resolutionLabelKey`, `fontFamilyLabelKey` are defined verbatim in both components; `RenderForm` additionally has `fontSizeLabelKey` and `titleCardDurationKey`. Each does `return \`settings.option.template.${value}\` as TranslationKey`. The key-shape knowledge is scattered.

### Fix

Add these key-construction helpers to `lib/i18n/messages.ts` (next to `t`) so the key shapes live beside their namespace:

```ts
export const optionKey = {
  template: (v: string) => `settings.option.template.${v}` as TranslationKey,
  resolution: (v: string) => `settings.option.resolution.${v}` as TranslationKey,
  fontFamily: (v: string) => `settings.option.fontFamily.${v}` as TranslationKey,
  fontPreset: (v: string) => `settings.option.fontPreset.${v}` as TranslationKey,
  titleCardDuration: (v: number) => `render.titleCard.duration.${v}` as TranslationKey,
};
```

Update both `SettingsForm.tsx` and `RenderForm.tsx` to import and use these, deleting their local copies.

### Verify

- `pnpm test` / `pnpm lint` / `pnpm build` pass.
- Both forms still render translated option labels for every option list.

---

## Issue 5 — Locale error string hardcodes the allowed set

### Severity: Low

### Files

- `delivery/webapp/src/app/api/settings/route.ts:141`

### Problem

```ts
if (b.locale !== undefined && !isLocale(b.locale)) {
  return NextResponse.json({ error: 'locale must be one of: "en", "zh-Hant"' }, { status: 400 });
}
```

Every sibling validator builds its message from its `VALID_*` const (`${VALID_TEMPLATES.join(", ")}`, etc.). The locale branch hardcodes the values instead of reusing the canonical `LOCALES`/`isLocale`, so the message drifts if a locale is added.

### Fix

Match the sibling pattern:

```ts
if (b.locale !== undefined && !isLocale(b.locale)) {
  return NextResponse.json({ error: `locale must be one of: ${LOCALES.join(", ")}` }, { status: 400 });
}
```

(The other `"en"` literals — schema default, `DEFAULTS`, `settings/page.tsx` `DEFAULT_SETTINGS` — are typed as `Locale` by the `as const` const array and are not error-message sources; leave them.)

### Verify

- `PUT /api/settings` with `{ locale: "fr" }` returns `400` with `locale must be one of: en, zh-Hant`.

---

## Issue 6 — Home page converted to a client component

### Severity: Low (SSR)

### Files

- `delivery/webapp/src/app/page.tsx`

### Problem

`page.tsx` was converted to `"use client"` purely to call `useLocale()`, turning the landing page into a client component. Its content (`home.title`, `home.subtitle`, `home.viewSongsets`) is all chrome.

### Fix

Revert `page.tsx` to a Server Component using the pure `t()` from the messages module with the server-resolved locale:

```tsx
import { resolveUserLocale } from "@/lib/i18n/server";
import { t } from "@/lib/i18n/messages";

export default async function HomePage() {
  const locale = await resolveUserLocale();   // no-arg: reads cookie via next/headers
  // ... render t(locale, "home.title") etc.
}
```

**Also update the root layout** (`layout.tsx:36`): drop the argument — `await resolveUserLocale()` — since the signature now reads the cookie internally. Its call is the only other site.

(One extra `resolveUserLocale` call per landing-page render, matching what the root layout already does; the landing page is a low-traffic shell. If the extra DB read is a concern, the layout could pass `locale` to children instead — deferred unless it shows up in profiling.)

### Verify

- `curl` the home page with `sow_locale=zh-Hant`: HTML contains `zh-Hant` heading text and no client-side hydration requirement for the copy.

---

## Issue 7 — Unrelated docs commit rides in this PR

### Severity: Low (scope)

### Files

- `docs/adr/0003-fixed-five-phase-worship-arc.md` (commit `f66a0523`)
- `CONTEXT.md` glossary expansion (same commit)

### Problem

Issue #143's scope is the bilingual UI. Commit `f66a0523` ("docs: expand domain model glossary and document fixed five-phase worship arc") is unrelated domain modeling that rode into the PR.

### Fix

Split `f66a0523` out of PR #144 so the PR contains only the bilingual feature:

```bash
git checkout main
git cherry-pick f66a0523     # land on its own branch / PR (ADR 0003 + glossary)
# then on sow_chinese_menus: drop the docs commit
git rebase --onto <new-base> f66a0523 sow_chinese_menus
git push --force-with-lease   # branch is pushed with an open PR #144 — history rewrite requires force-push
```

If a single-commit history is not required, an alternative is to keep it but record the attribution in the PR body (already done) and accept the mixed scope — flag for the author's call. Recommendation: split, to keep #144 unambiguous against #143.

### Verify

- `git log main..HEAD` on the rebased branch lists only `925de966` (bilingual UI).
- The docs commit lands independently on its own branch/PR.

---

## Test changes

- `mergeMessages` collision test (Issue 2): assert importing a colliding bundle throws.
- `formatTotalDuration` unit test across en/zh-Hant (Issue 3).
- Settings PUT `Set-Cookie` assertion + `resolveUserLocale` cookie fallback unit test (Issue 1).

## Out of scope

- Any Simplified-Chinese work, catalog/lyrics translation, Android, render-worker, admin CLI, URL locale routing, `Accept-Language` detection — all still out of scope per issue #143.
