# Webapp User Registration Page (v2)

**Component:** Next.js Webapp (`webapp/`)
**Status:** Draft
**Created:** 2026-05-16
**Supersedes:** `specs/webapp-register-page.md` (v1)

## Changes from v1

- **Server config change added** — raise Better Auth `maxPasswordLength` from default 32 to 128 (avoid silent server rejection of password-manager-generated passwords).
- **Redirect mirrors login exactly** — `router.push("/songsets")` **followed by** `router.refresh()`. Without `refresh()`, the proxy doesn't re-evaluate with the new session cookie and can bounce back to `/login`.
- **Mock shape corrected** — `signUp` is namespaced like `signIn`, so the mock is `signUp: { email: vi.fn() }`, not a bare `vi.fn()`.
- **`register.test.tsx` mock factory spelled out** — full module surface so imports don't break.
- **Verification section added** — explicit end-to-end checks.

## Overview

Add a `/register` page to the webapp so new users can self-service create accounts. Better Auth's `signUp.email()` handles everything — creates the `user` row and `account` row (with bcrypt-hashed password) in one atomic operation. No manual password hashing or `account` table insertion needed.

## Current State

- `signUp` is already exported from `webapp/src/lib/auth-client.ts:7` but unused
- Login page (`/login`) exists with email/password form at `webapp/src/app/login/page.tsx`
- Route proxy (`webapp/src/proxy.ts`) only allows unauthenticated access to `/login` and `/api/auth`
- Better Auth server config at `webapp/src/lib/auth.ts:18-20` has `emailAndPassword: { enabled: true }` — no custom `maxPasswordLength` (defaults to 32)
- No registration UI exists anywhere

## Implementation Plan

### Task 1: Raise server-side password max length

**File:** `webapp/src/lib/auth.ts` (modify)

Better Auth caps passwords at 32 chars by default. That's too short for password managers — users will be silently rejected with a generic error after passing client validation.

Change `webapp/src/lib/auth.ts:18-20` from:
```ts
emailAndPassword: {
  enabled: true,
},
```
to:
```ts
emailAndPassword: {
  enabled: true,
  maxPasswordLength: 128,
},
```

### Task 2: Create `/register` page

**File:** `webapp/src/app/register/page.tsx` (new)

Mirror the login page structure. Key differences:
- Add **name** field (required by `signUp.email()`)
- Add **confirm password** field
- Call `signUp.email({ email, password, name })` instead of `signIn.email()`
- Better Auth auto-signs-in after successful registration. On success: `router.push("/songsets")` **then** `router.refresh()` (matches `webapp/src/app/login/page.tsx:47-48` — the `refresh()` ensures `proxy.ts` re-runs with the new session cookie)
- Link to `/login`: "Already have an account? Sign in"

**Validation rules:**
| Field | Rule |
|-------|------|
| Name | Required |
| Email | Required, valid format (`/^[^\s@]+@[^\s@]+\.[^\s@]+$/`) |
| Password | Required, >= 8 characters |
| Confirm password | Required, must match password |

(No client-side max-length check — server cap raised to 128 in Task 1.)

**Error handling:**
- `signUp.email()` returns `{ error }` on failure (e.g. duplicate email)
- Display error message in form-level error area (same pattern as login page)

**Component structure:**
```tsx
"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { signUp } from "@/lib/auth-client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export default function RegisterPage() {
  // state: name, email, password, confirmPassword, errors, loading
  // validate(): name required, email format, password >= 8, passwords match
  // handleSubmit():
  //   - signUp.email({ email, password, name })
  //   - on success: router.push("/songsets"); router.refresh();
  //   - on error: setErrors({ form: result.error.message ?? "Registration failed" })
  // render: Card with name/email/password/confirmPassword fields + submit + login link
}
```

### Task 3: Add register link to login page

**File:** `webapp/src/app/login/page.tsx` (modify)

Add below the submit button:
```tsx
<p className="text-center text-sm text-muted-foreground">
  Don't have an account?{" "}
  <a href="/register" className="text-primary underline-offset-4 hover:underline">
    Register
  </a>
</p>
```

### Task 4: Add `/register` to public paths

**File:** `webapp/src/proxy.ts` (modify)

```ts
const PUBLIC_PATHS = ["/login", "/register", "/api/auth"];
```

Matching at `webapp/src/proxy.ts:7` is `pathname === p || pathname.startsWith(p + "/")` — exact-or-trailing-slash, so `/register` matches `/register` only and won't false-positive on `/registerSomething`.

### Task 5: Create register page tests

**File:** `webapp/src/test/auth/register.test.tsx` (new)

Same pattern as `webapp/src/test/auth/login.test.tsx`. Use `vi.hoisted` for the mock fns. Full mock factory:

```ts
const { mockPush, mockRefresh, mockSignUp } = vi.hoisted(() => ({
  mockPush: vi.fn(),
  mockRefresh: vi.fn(),
  mockSignUp: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush, refresh: mockRefresh }),
}));

vi.mock("@/lib/auth-client", () => ({
  signIn: { email: vi.fn() },
  signOut: vi.fn(),
  useSession: vi.fn(() => ({ data: null, isPending: false })),
  signUp: { email: mockSignUp },
}));
```

**Test cases:**
| Test | Description |
|------|-------------|
| Renders all fields | Name, email, password, confirm password, submit button |
| Name required | Shows error when name is empty |
| Email required | Shows error when email is empty |
| Invalid email format | Shows error for malformed email |
| Password required | Shows error when password is empty |
| Password too short | Shows error when password < 8 chars |
| Passwords must match | Shows error when confirm != password |
| Calls signUp.email | Submits with correct `{ email, password, name }` |
| Redirects on success | Asserts **both** `mockPush` called with `/songsets` and `mockRefresh` called |
| Shows error on duplicate email | Displays error from `signUp.email` failure |
| Loading state | Button shows "Creating account..." and is disabled |

### Task 6: Update auth-client mock in auth-context test

**File:** `webapp/src/test/auth/auth-context.test.tsx` (modify)

Currently only mocks `signIn`, `signOut`, `useSession` (verified at `webapp/src/test/auth/auth-context.test.tsx:9-13`). Add the namespaced `signUp`:

```ts
signUp: { email: vi.fn() },
```

(Note: shape is `{ email: vi.fn() }` to match the real client surface — not a bare `vi.fn()`.)

## Files to Modify/Create

| File | Action | Purpose |
|------|--------|---------|
| `webapp/src/lib/auth.ts` | Modify | Raise `maxPasswordLength` to 128 |
| `webapp/src/app/register/page.tsx` | Create | Registration form page |
| `webapp/src/app/login/page.tsx` | Modify | Add link to `/register` |
| `webapp/src/proxy.ts` | Modify | Add `/register` to public paths |
| `webapp/src/test/auth/register.test.tsx` | Create | Registration page tests |
| `webapp/src/test/auth/auth-context.test.tsx` | Modify | Add `signUp` to mock |

## Verification

- `pnpm --filter webapp test` — all auth tests pass, including new `register.test.tsx`
- `pnpm --filter webapp typecheck`
- Boot dev server (`pnpm --filter webapp dev`), visit `/register`, create a new user, confirm landing on `/songsets` without bouncing back to `/login`
- Inspect DB: confirm new row in `user` table and corresponding `account` row with bcrypt-hashed password
- Sign out, then `/login` with the new credentials to confirm round-trip works
- Try a 40-char password (e.g. from a password manager) to confirm Task 1's `maxPasswordLength: 128` change took effect

## Out of Scope

- **Admin CLI password support** (`sow-admin users add --password`) — deferred; requires manual bcrypt + `account` row insertion
- **Email verification flow** — Better Auth supports it but not configured
- **OAuth providers** — Google recommended in specs but not yet implemented
- **Password strength meter** — nice-to-have, not in this scope
- **Forgot password / reset password** — separate feature

## Decision Rationale

| Decision | Rationale |
|----------|-----------|
| Use `signUp.email()` instead of manual DB insertion | Better Auth handles user + account row creation + bcrypt hashing atomically. Replicating this in Python is fragile and version-coupled. |
| Raise `maxPasswordLength` to 128 (vs. add client max-32 check) | Better Auth's default 32 is too restrictive for modern password managers. Raising the cap is friendlier than adding an artificial client limit. |
| `router.push` + `router.refresh` after signup | Matches login pattern; `refresh()` re-runs `proxy.ts` so it sees the new session cookie set by `nextCookies()` plugin. Without it, the redirect can land on a protected page that bounces back to `/login`. |
| Confirm password field | Prevents typos during registration since password is masked |
| Auto-redirect after registration | Better Auth auto-creates a session on signUp — no separate login step needed |
| Namespaced mock shape `signUp: { email: vi.fn() }` | Matches the real Better Auth client surface; bare `vi.fn()` would break any code that calls `signUp.email()` |
