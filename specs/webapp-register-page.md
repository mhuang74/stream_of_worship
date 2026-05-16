# Webapp User Registration Page

**Component:** Next.js Webapp (`webapp/`)
**Status:** Draft
**Created:** 2026-05-16

## Overview

Add a `/register` page to the webapp so new users can self-service create accounts. Better Auth's `signUp.email()` handles everything — creates the `user` row and `account` row (with bcrypt-hashed password) in one atomic operation. No manual password hashing or `account` table insertion needed.

## Current State

- `signUp` is already exported from `webapp/src/lib/auth-client.ts` but unused
- Login page (`/login`) exists with email/password form
- Route proxy (`proxy.ts`) only allows unauthenticated access to `/login` and `/api/auth`
- No registration UI exists anywhere

## Implementation Plan

### Task 1: Create `/register` page

**File:** `webapp/src/app/register/page.tsx` (new)

Mirror the login page structure. Key differences:
- Add **name** field (required by `signUp.email()`)
- Add **confirm password** field
- Call `signUp.email({ email, password, name })` instead of `signIn.email()`
- Better Auth auto-signs-in after successful registration, so redirect to `/songsets` on success
- Link to `/login`: "Already have an account? Sign in"

**Validation rules:**
| Field | Rule |
|-------|------|
| Name | Required |
| Email | Required, valid format (`/^[^\s@]+@[^\s@]+\.[^\s@]+$/`) |
| Password | Required, >= 8 characters |
| Confirm password | Required, must match password |

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
  // handleSubmit(): signUp.email({ email, password, name }), redirect on success
  // render: Card with name/email/password/confirmPassword fields + submit + login link
}
```

### Task 2: Add register link to login page

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

### Task 3: Add `/register` to public paths

**File:** `webapp/src/proxy.ts` (modify)

```ts
const PUBLIC_PATHS = ["/login", "/register", "/api/auth"];
```

### Task 4: Create register page tests

**File:** `webapp/src/test/auth/register.test.tsx` (new)

Same pattern as `login.test.tsx`. Mock `signUp` from `@/lib/auth-client`.

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
| Redirects on success | Pushes `/songsets` after successful registration |
| Shows error on duplicate email | Displays error from `signUp.email` failure |
| Loading state | Button shows "Creating account..." and is disabled |

### Task 5: Update auth-client mock in auth-context test

**File:** `webapp/src/test/auth/auth-context.test.tsx` (modify)

Add `signUp: vi.fn()` to the `@/lib/auth-client` mock (currently only mocks `signIn`, `signOut`, `useSession`).

## Files to Modify/Create

| File | Action | Purpose |
|------|--------|---------|
| `webapp/src/app/register/page.tsx` | Create | Registration form page |
| `webapp/src/app/login/page.tsx` | Modify | Add link to `/register` |
| `webapp/src/proxy.ts` | Modify | Add `/register` to public paths |
| `webapp/src/test/auth/register.test.tsx` | Create | Registration page tests |
| `webapp/src/test/auth/auth-context.test.tsx` | Modify | Add `signUp` to mock |

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
| Confirm password field | Prevents typos during registration since password is masked |
| Auto-redirect after registration | Better Auth auto-creates a session on signUp — no separate login step needed |
| No new dependencies | `signUp` is already exported from `auth-client.ts`; all UI components exist |
