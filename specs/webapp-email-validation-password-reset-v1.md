# Webapp Email Validation + Password Reset + Settings Split (v1)

**Component:** Next.js Webapp (`delivery/webapp/`)
**Status:** Draft
**Created:** 2026-08-24

## Overview

Add email validation to the sign-up flow, use the validated email for password
reset, add a "forgot password" link on the Sign In page, and split Settings
into Account (name + password) and Preferences (existing settings form).

## Decisions (settled via grilling)

| Decision | Choice |
|----------|--------|
| Email delivery | **Resend** (`resend` dep + `RESEND_API_KEY` / `RESEND_FROM_ADDRESS`) |
| Verification enforcement | `requireEmailVerification: true`; **backfill existing users to `emailVerified=true`** |
| Resend verification | Yes — post-signup screen + login error for unverified users |
| Post-verification | **Auto sign-in** → dashboard |
| Reset flow | Dedicated `/forgot-password` + `/reset-password` pages; **no auto sign-in** after reset → `/login` |
| Settings split | Account = name + password only; email change **out of scope** |
| Password change | **Require current password** (`changePassword`) |

## Current State

- Better Auth 1.6.11 at `delivery/webapp/src/lib/auth.ts` with
  `emailAndPassword: { enabled: true, maxPasswordLength: 128 }` — no email
  verification, no password reset, no mailer.
- `users.emailVerified` column exists (default `false`); `verifications` table
  exists (Better Auth standard).
- No mailer dependency anywhere in the webapp. Resend was previously spec'd in
  `specs/simplify-render-progress-notification-v1.md` but never implemented.
- Register (`src/app/register/page.tsx`): `signUp.email({ email, password, name })`
  → auto-signs-in → `router.push("/")`.
- Login (`src/app/login/page.tsx`): `signIn.email()` → redirect (honors
  `callbackUrl`).
- Settings (`src/app/settings/page.tsx`): single `SettingsForm` (all
  preferences) + a bare "Sign out" block at the bottom.
- i18n: two locales (`en`, `zh-Hant`); auth + settings keys in
  `src/lib/i18n/messages/core.ts`.
- Proxy public paths (`src/proxy.ts`): `/`, `/login`, `/register`, `/api/auth`,
  `/share`, `/api/share`.

## Implementation Plan

### Phase 0 — Dependency & config

**`delivery/webapp/package.json`** — add `resend` (`pnpm add resend`).

**`delivery/webapp/.env.example`** + **`.env.production.example`** — add:

```
RESEND_API_KEY=
RESEND_FROM_ADDRESS=Stream of Worship <noreply@streamofworship.com>
```

### Phase 1 — Email client

**`delivery/webapp/src/lib/email/client.ts`** (new) — thin Resend wrapper:

- `sendVerificationEmail({ to, url })` — subject "Verify your email", body with
  the link.
- `sendPasswordResetEmail({ to, url })` — subject "Reset your password", body
  with the link.
- No-op-safe: if `RESEND_API_KEY` is unset, log and skip (dev without a key
  still works; production requires it).

### Phase 2 — Auth server config

**`delivery/webapp/src/lib/auth.ts`** — extend `emailAndPassword`:

```ts
emailAndPassword: {
  enabled: true,
  maxPasswordLength: 128,
  requireEmailVerification: true,
  sendVerificationEmail: async ({ user, url }) =>
    sendVerificationEmail({ to: user.email, url }),
  sendResetPassword: async ({ user, url }) =>
    sendPasswordResetEmail({ to: user.email, url }),
}
```

`autoSignInAfterVerification` stays default `true` → matches "auto sign-in
after verification".

> **Implementation note:** verify the exact nesting of `sendVerificationEmail`
> against the installed package's TypeScript types at implementation time. In
> Better Auth 1.6.x, `sendVerificationEmail` lives under a top-level
> `emailVerification` block (alongside `sendOnSignUp`,
> `autoSignInAfterVerification`), while `sendResetPassword` lives under
> `emailAndPassword`. Wrong nesting fails silently or at typecheck.

### Phase 3 — Backfill migration

**`delivery/webapp/drizzle/0023_backfill_email_verified.sql`** (new,
hand-written):

```sql
UPDATE "user" SET "emailVerified" = true WHERE "emailVerified" = false;
```

Rationale: existing users already proved ownership by using the app;
enforcement must not lock them out. drizzle-kit cannot express this cleanly, so
hand-written SQL (consistent with the `0018_theme_anchors.sql` precedent).

### Phase 4 — Register page (verification gate)

**`delivery/webapp/src/app/register/page.tsx`** — change success path. With
`requireEmailVerification`, `signUp.email()` no longer auto-signs-in. On
success:

- Show a "check your email" confirmation state (email + "resend" button)
  instead of `router.push("/")`.
- Resend button → `sendVerificationEmail({ email, callbackURL: "/" })`.
- Keep the locale-persist best-effort PUT.

### Phase 5 — Login page (forgot link + unverified handling)

**`delivery/webapp/src/app/login/page.tsx`**:

- Add "Forgot password?" link → `/forgot-password`.
- On `signIn.email()` error indicating an unverified email, surface a "resend
  verification" action (calls `sendVerificationEmail`).

### Phase 6 — Forgot + reset pages

**`delivery/webapp/src/app/forgot-password/page.tsx`** (new) — email input →
`forgetPassword({ email, redirectTo: "/reset-password" })` → "if that email
exists, a reset link was sent" (no account enumeration).

**`delivery/webapp/src/app/reset-password/page.tsx`** (new) — read `token` from
URL query → new password + confirm → `resetPassword({ newPassword, token })` →
redirect `/login` (no auto sign-in).

**`delivery/webapp/src/lib/auth-client.ts`** — export `forgetPassword`,
`resetPassword`, `changePassword`, `updateUser`, `sendVerificationEmail`.

**`delivery/webapp/src/proxy.ts`** — add `/forgot-password`, `/reset-password`
to `PUBLIC_PATHS`.

### Phase 7 — Settings split

**`delivery/webapp/src/app/settings/page.tsx`** — restructure into two
sections:

- **Account** (new `AccountSettings` component): change name
  (`updateUser({ name })`), change password
  (`changePassword({ currentPassword, newPassword })`), sign out.
- **Preferences**: existing `SettingsForm` unchanged.

**`delivery/webapp/src/components/settings/AccountSettings.tsx`** (new) — name
field + current/new/confirm password fields, each with its own submit +
validation + error handling.

### Phase 8 — i18n

**`delivery/webapp/src/lib/i18n/messages/core.ts`** — add keys (both `en` +
`zh-Hant`): forgot/reset page strings, verification strings, account settings
strings, resend actions.

### Phase 9 — Tests

- **`src/test/auth/forgot-password.test.tsx`** (new)
- **`src/test/auth/reset-password.test.tsx`** (new)
- **`src/test/components/settings/AccountSettings.test.tsx`** (new)
- **`src/test/auth/register.test.tsx`** — update: success now shows
  verification state, not redirect.
- **`src/test/auth/login.test.tsx`** — update: forgot link present; unverified
  error path.

## Files to Modify/Create

| File | Action | Purpose |
|------|--------|---------|
| `delivery/webapp/package.json` | Modify | Add `resend` dep |
| `delivery/webapp/.env.example` | Modify | Add Resend env vars |
| `delivery/webapp/.env.production.example` | Modify | Add Resend env vars |
| `delivery/webapp/src/lib/email/client.ts` | Create | Resend email client |
| `delivery/webapp/src/lib/auth.ts` | Modify | Enable verification + reset senders |
| `delivery/webapp/drizzle/0023_backfill_email_verified.sql` | Create | Backfill existing users |
| `delivery/webapp/src/app/register/page.tsx` | Modify | Verification gate on success |
| `delivery/webapp/src/app/login/page.tsx` | Modify | Forgot link + unverified handling |
| `delivery/webapp/src/app/forgot-password/page.tsx` | Create | Forgot password form |
| `delivery/webapp/src/app/reset-password/page.tsx` | Create | Reset password form |
| `delivery/webapp/src/lib/auth-client.ts` | Modify | Export new auth methods |
| `delivery/webapp/src/proxy.ts` | Modify | Add public paths |
| `delivery/webapp/src/app/settings/page.tsx` | Modify | Split Account vs Preferences |
| `delivery/webapp/src/components/settings/AccountSettings.tsx` | Create | Name + password change |
| `delivery/webapp/src/lib/i18n/messages/core.ts` | Modify | New i18n keys |
| `delivery/webapp/src/test/auth/forgot-password.test.tsx` | Create | Forgot page tests |
| `delivery/webapp/src/test/auth/reset-password.test.tsx` | Create | Reset page tests |
| `delivery/webapp/src/test/components/settings/AccountSettings.test.tsx` | Create | Account settings tests |
| `delivery/webapp/src/test/auth/register.test.tsx` | Modify | Verification state tests |
| `delivery/webapp/src/test/auth/login.test.tsx` | Modify | Forgot link + unverified tests |

## Verification

- `pnpm --filter sow-webapp typecheck`
- `pnpm --filter sow-webapp test`
- Browser-drive the real flow (per AGENTS.md recipe): register → verify →
  sign-in; forgot → reset → sign-in; settings name/password change.

## Out of Scope

- Email change (would require re-verification of the new address).
- OAuth providers.
- Password strength meter.
- Admin CLI password support.

## Decision Rationale

| Decision | Rationale |
|----------|-----------|
| Resend | Already spec'd in this repo; free tier 100 emails/day; simple SDK. |
| Enforce + backfill existing to verified | Existing users already proved ownership by using the app; enforcement must not lock them out. |
| Dedicated forgot/reset pages | Standard, matches Better Auth `forgetPassword`/`resetPassword`. |
| No auto sign-in after reset | Reset is a credential-recovery moment, not an ownership proof; redirect to `/login`. |
| Auto sign-in after verification | The user just proved they own the inbox; smoother than a second login. |
| Account = name + password only | Email change requires re-verification and a separate flow; out of scope. |
| Require current password | Re-authenticate before changing a credential; matches `changePassword` signature. |
| Hand-written backfill SQL | drizzle-kit cannot express "set all to true" cleanly; matches `0018_theme_anchors.sql` precedent. |
