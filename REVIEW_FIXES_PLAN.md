# Review Fixes: Email Validation + Password Reset + Settings Split

## Context

A two-axis code review of commit `fb2e414c` (email validation, password reset, settings split) found 5 code-smell findings (all judgement calls) and 1 spec bug. This plan addresses all 5 standards findings plus the locale-persist inconsistency. The two scope-creep items (open-redirect guard in login, INVALID_TOKEN card in reset-password) are kept — both are sensible completions.

## Approach

### Step 1 — Extract `sendEmail` helper in `src/lib/email/client.ts`

Refactor `sendVerificationEmail` and `sendPasswordResetEmail` to delegate to one private `sendEmail` function. Add this private function (no equivalent exists):

```ts
interface SendEmailParams {
  to: string;
  subject: string;
  html: string;
  skipLabel: string;
  errorLabel: string;
}

async function sendEmail({ to, subject, html, skipLabel, errorLabel }: SendEmailParams): Promise<void> {
  const resend = getResend();
  if (!resend) {
    console.warn(`[email] RESEND_API_KEY not set; skipping ${skipLabel} to ${to}`);
    return;
  }
  const { error } = await resend.emails.send({ from: fromAddress(), to, subject, html });
  if (error) console.error(`[email] ${errorLabel}:`, error);
}
```

Rewrite `sendVerificationEmail` to call `sendEmail({ to, subject: "Verify your email", html: \`...\`, skipLabel: "verification email", errorLabel: "Failed to send verification email" })`. Same for `sendPasswordResetEmail` with `subject: "Reset your password"`, `skipLabel: "password reset email"`, `errorLabel: "Failed to send reset email"`. Keep the same `html` bodies and `EmailSendArgs` interface. The `url` param stays in each public function's signature; it's interpolated into `html` before calling `sendEmail`.

The skip warn drops the `(${url})` suffix — dev-only debug, `to` address is the useful part.

### Step 2 — Create `src/lib/validation.ts` (new file)

No existing validation utility in the codebase. Add:

```ts
export const MIN_PASSWORD_LENGTH = 8;

export function isValidEmail(email: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
}
```

Do NOT extract a `passwordsMatch` helper — it would be a Middle Man wrapping `===`. The confirm-mismatch checks (`confirmPassword !== password`) stay inline; they're trivial comparisons tied to specific form field names.

### Step 3 — Create `src/lib/persist-locale.ts` (new file)

No equivalent exists. Add:

```ts
export async function persistLocale(locale: string): Promise<void> {
  try {
    await fetch("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ locale }),
    });
  } catch {
    // best-effort
  }
}
```

### Step 4 — Rename `sendVerificationEmail` → `requestVerificationEmail` in auth-client + all callsites

Disambiguates from `email/client.ts`'s `sendVerificationEmail` (server-side Resend sender). The two are different functions with the same name — one is a Better Auth client action, the other is a server email sender.

**`src/lib/auth-client.ts`** — change export key `sendVerificationEmail` → `requestVerificationEmail` (line 16).

**`src/app/login/page.tsx`** — import: `sendVerificationEmail` → `requestVerificationEmail` (line 7); call site at line 31.

**`src/app/register/page.tsx`** — import: `sendVerificationEmail` → `requestVerificationEmail` (line 5); call site at line 99.

**`src/test/auth/login.test.tsx`** — hoisted var `mockSendVerificationEmail` → `mockRequestVerificationEmail` (line 10); mock key `sendVerificationEmail` → `requestVerificationEmail` (line 24); references at lines 212, 224.

**`src/test/auth/register.test.tsx`** — hoisted var `mockSendVerificationEmail` → `mockRequestVerificationEmail` (line 10); mock key `sendVerificationEmail` → `requestVerificationEmail` (line 25); references at lines 147, 159.

**`src/test/app/settings-save-error.test.tsx`** — mock key `sendVerificationEmail` → `requestVerificationEmail` (line 24). Not called by tested code but keeps mock consistent with actual exports.

**`src/test/app/settings-signout.test.tsx`** — mock key `sendVerificationEmail` → `requestVerificationEmail` (line 26). Same reason.

**NOT affected:** `src/lib/auth.ts` line 6 imports `sendVerificationEmail` from `@/lib/email/client` — a different module, not the auth-client export.

### Step 5 — Create `src/hooks/useResendVerification.ts` (new file)

Follows `useSignOut.ts` pattern (same directory, `"use client"` directive, `useCallback` + `useState`).

```ts
"use client";

import { useCallback, useState } from "react";
import { requestVerificationEmail } from "@/lib/auth-client";

export function useResendVerification(email: string | null): {
  resending: boolean;
  resendState: "idle" | "sent" | "error";
  resend: () => Promise<void>;
} {
  const [resending, setResending] = useState(false);
  const [resendState, setResendState] = useState<"idle" | "sent" | "error">("idle");

  const resend = useCallback(async () => {
    if (!email) return;
    setResending(true);
    setResendState("idle");
    try {
      const result = await requestVerificationEmail({ email, callbackURL: "/" });
      setResendState(result.error ? "error" : "sent");
    } catch {
      setResendState("error");
    } finally {
      setResending(false);
    }
  }, [email]);

  return { resending, resendState, resend };
}
```

### Step 6 — Update `src/app/login/page.tsx`

Three changes in one pass:

1. **Use hook**: Remove inline `resending`/`resendState` state (lines 23-24) and `handleResendVerification` function (lines 26-41). Import and call `useResendVerification(unverifiedEmail)`. Replace `handleResendVerification` calls in JSX with `resend`, `resending` and `resendState` references stay the same (hook returns them).

2. **Use validation utils**: Import `isValidEmail`, `MIN_PASSWORD_LENGTH` from `@/lib/validation`. In `validate()`: replace `!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)` with `!isValidEmail(email)` (line 47); replace `password.length < 8` with `password.length < MIN_PASSWORD_LENGTH` (line 52).

3. **Use persistLocale**: Import `persistLocale` from `@/lib/persist-locale`. Replace the inline `try { await fetch("/api/settings", ...) } catch {}` block (lines 82-90) with `await persistLocale(locale);`.

### Step 7 — Update `src/app/register/page.tsx`

Four changes in one pass:

1. **Use hook**: Remove inline `resending`/`resendState` state (lines 31-32) and `handleResend` function (lines 94-109). Import and call `useResendVerification(submittedEmail)`. Replace `handleResend` calls in JSX with `resend`.

2. **Use validation utils**: Same as login — replace email regex (line 41) with `!isValidEmail(email)`; replace `password.length < 8` (line 46) with `password.length < MIN_PASSWORD_LENGTH`.

3. **Use persistLocale + remove locale guard**: Replace the `if (locale !== "en") { try { ... } catch {} }` block (lines 74-84) with `await persistLocale(locale);` — fires unconditionally, matching login behavior. This fixes the spec finding: register now persists `en` locale too, overriding any previously saved non-en locale.

4. **Remove now-unused imports**: After removing inline state/handler, `sendVerificationEmail`/`requestVerificationEmail` is no longer imported directly (the hook handles it). Remove it from the import line. Keep `signUp` import.

### Step 8 — Update `src/app/forgot-password/page.tsx`

Import `isValidEmail` from `@/lib/validation`. In `validate()`: replace `!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)` (line 26) with `!isValidEmail(email)`.

### Step 9 — Update `src/app/reset-password/page.tsx`

Import `MIN_PASSWORD_LENGTH` from `@/lib/validation`. In `validate()`: replace `password.length < 8` (line 34) with `password.length < MIN_PASSWORD_LENGTH`.

### Step 10 — Split `src/components/settings/AccountSettings.tsx`

Create two new files, modify AccountSettings to compose them.

**`src/components/settings/NameForm.tsx`** (new) — extract the name form:
- Imports: `useEffect, useRef, useState` from react; `updateUser, useSession` from `@/lib/auth-client`; `Button`, `Input`, `Label` from UI components; `useLocale` from `@/hooks/useLocale`.
- Moves: `NameErrors` interface, name state, `nameInitialized` ref + effect, `nameErrors`/`savingName`/`nameSaved` state, `handleNameSubmit` function, name form JSX (lines 135-164 of current AccountSettings).
- Imports `MIN_PASSWORD_LENGTH`? No — name form doesn't validate password. No validation utils needed here.
- Exported as `export function NameForm()`.

**`src/components/settings/PasswordChangeForm.tsx`** (new) — extract the password form:
- Imports: `useState` from react; `changePassword` from `@/lib/auth-client`; `Button`, `Input`, `Label` from UI components; `useLocale` from `@/hooks/useLocale`; `MIN_PASSWORD_LENGTH` from `@/lib/validation`.
- Moves: `PasswordErrors` interface, password state, `passwordErrors`/`savingPassword`/`passwordSaved` state, `handlePasswordSubmit` function (with INVALID_PASSWORD handling), password form JSX (lines 166-236).
- Uses `MIN_PASSWORD_LENGTH` instead of `8` in `handlePasswordSubmit` (line 93).
- Exported as `export function PasswordChangeForm()`.

**`src/components/settings/AccountSettings.tsx`** (modified):
- Remove all moved state, interfaces, handlers, and form JSX.
- Import `NameForm` and `PasswordChangeForm`.
- Keep: `useSignOut` import, `useLocale` for `t`, sign-out `Button` JSX.
- Render: `<NameForm />`, `<PasswordChangeForm />`, sign-out `<Button>` (lines 238-246).
- Keep the component comment (lines 12-14) but trim to reflect that AccountSettings is now a composition wrapper.

### Step 11 — Remove redundant `autoSignInAfterVerification: true` from `src/lib/auth.ts`

Delete lines 48-50 (the comment + `autoSignInAfterVerification: true`). This is the Better Auth default; the spec says "stays default true." The `emailVerification` block keeps only `sendVerificationEmail`.

### Step 12 — Update register test for locale-persist fix

**`src/test/auth/register.test.tsx`** — the test at line 220 "does not call the settings API when locale is en" asserts `expect(mockFetch).not.toHaveBeenCalled()`. After step 7, register fires the PUT unconditionally. Rewrite this test to:

```
it("persists en locale via settings PUT on successful registration (overrides saved locale)", async () => {
  mockSignUp.mockResolvedValue({ data: { user: { id: "1" } }, error: null });
  renderWithLocale(<RegisterPage />);
  await userEvent.type(screen.getByLabelText("Name"), "Test User");
  await userEvent.type(screen.getByLabelText("Email"), "user@example.com");
  await userEvent.type(screen.getByLabelText("Password"), "password123");
  await userEvent.type(screen.getByLabelText("Confirm password"), "password123");
  fireEvent.click(screen.getByRole("button", { name: /create account/i }));
  await waitFor(() => {
    expect(mockFetch).toHaveBeenCalledWith(
      "/api/settings",
      expect.objectContaining({
        method: "PUT",
        body: JSON.stringify({ locale: "en" }),
      })
    );
  });
  await waitFor(() => {
    expect(screen.getByText("Check your email")).toBeInTheDocument();
  });
});
```

This mirrors the login test at line 168 ("persists en locale via settings PUT on successful login (overrides saved locale)").

## Critical files & anchors

- `delivery/webapp/src/lib/email/client.ts` — `sendVerificationEmail`/`sendPasswordResetEmail` (lines 31-74): extract shared `sendEmail` skeleton.
- `delivery/webapp/src/app/register/page.tsx` — `handleSubmit` locale guard (line 74): remove `if (locale !== "en")`, fire `persistLocale` unconditionally; `handleResend` (lines 94-109): replaced by hook.
- `delivery/webapp/src/components/settings/AccountSettings.tsx` — full file: split into `NameForm.tsx` + `PasswordChangeForm.tsx` + trimmed parent.
- `delivery/webapp/src/lib/auth-client.ts` — export rename (line 16): `sendVerificationEmail` → `requestVerificationEmail`, cascading to 6 callsite files.
- `delivery/webapp/src/test/auth/register.test.tsx` — locale-persist test (line 220): rewrite to assert PUT fires for `en`.

## Verification

1. **Typecheck**: `pnpm --filter sow-webapp typecheck` — confirms all imports resolve after rename + new files.
2. **Test suite**: `pnpm --filter sow-webapp test` — all existing tests must pass. Key tests to watch:
   - `register.test.tsx`: "persists en locale via settings PUT" (rewritten test) + "resends the verification email" (mock key renamed) + "persists zh-Hant locale" (still works with `persistLocale`).
   - `login.test.tsx`: "shows a resend-verification action" (mock key renamed) + "persists en locale" (still works with `persistLocale`).
   - `AccountSettings.test.tsx`: all 9 tests pass through the split components unchanged (queries by label/button name).
   - `settings-signout.test.tsx` + `settings-save-error.test.tsx`: mock key renamed, tests still pass.
3. **New-behavior check**: The register page locale-persist fix is the only behavior change. After `pnpm --filter sow-webapp test` passes, confirm the rewritten register test asserts `mockFetch` IS called with `{ locale: "en" }` — this proves register now persists `en` unconditionally, matching login.

## Assumptions & contingencies

- **`autoSignInAfterVerification` removal**: Removing the explicit `true` relies on Better Auth's default being `true`. If `pnpm --filter sow-webapp typecheck` or tests reveal the default is `false` in the installed version, restore the explicit `autoSignInAfterVerification: true` with its comment.
- **Hook test interaction**: `useResendVerification` is not mocked in tests — it calls the mocked `requestVerificationEmail` from `@/lib/auth-client`. If tests fail because the hook's `useCallback` dependency on `email` causes stale closures in test scenarios, add `email` to a ref inside the hook to stabilize the callback identity without changing the API.
