# Sign-Up and Email Verification

*Who this is for:* a developer joining the Stream of Worship web app who needs to understand, debug, or change the account-creation path.
*What it assumes:* you can read TypeScript, React, and HTTP. It does **not** assume you know Better Auth; the parts that matter are explained inline.
*How to read it:* §1–§2 are the plain-language overview; §3 onward walks the flow step by step, quoting the code that implements it.

Every code block below is quoted **verbatim** from this repository, and the fence title names the source file. Where a quote cites `delivery/webapp/node_modules/better-auth/...`, that is the library source actually installed in this repo (`better-auth@1.6.11`), quoted because the behavior lives there rather than in our code — it is a dependency, not something we maintain, and a version bump can change it. The one block with **no** fence title (§4, the memory-adapter seed) is illustrative rather than quoted; prose says so.

## Related documents

- [`specs/webapp-email-validation-password-reset-v1.md`](../specs/webapp-email-validation-password-reset-v1.md) — the spec this feature was built from (sign-up verification, password reset, settings split).
- [`docs/agents/domain.md`](agents/domain.md) — how domain docs and ADRs are organised in this repo.
- [`delivery/webapp/README.md`](../delivery/webapp/README.md) — webapp setup, dev server, environment variables.
- [`delivery/webapp/.env.production.example`](../delivery/webapp/.env.production.example) — the Resend variables in full production context.

---

## 1. The flow in plain language

1. **Fill out the form.** On `/register` the user enters name, email, password, and confirm password. The page checks these client-side before submitting: name isn't blank, the email looks like an email, the password is at least 8 characters, and the two passwords match.
2. **Account is created.** Submitting calls Better Auth's `signUp.email({ email, password, name })`, which hits `POST /api/auth/sign-up/email`. It saves a user row with `emailVerified = false` and a separate account row holding the hashed password.
3. **Verification email goes out.** Because the app sets `requireEmailVerification: true`, Better Auth generates a verification link and hands it to the app's mailer, which sends it through Resend. The link points at `/api/auth/verify-email?token=...&callbackURL=/`. The token is a signed JWT valid for one hour.
4. **User is not signed in yet.** Signup does not create a session. The response comes back with no auth cookie, so the user has an account but no way to use it until they verify.
5. **Confirmation screen.** Instead of redirecting anywhere, the page swaps to a "Check your email" card showing the address they signed up with, plus a **Resend verification email** button.
6. **Clicking the link.** The user opens the email and clicks through. The server checks the token, flips `emailVerified` to `true`, and redirects to `/`. Clicking an already-used link is harmless — it just redirects again.
7. **Signing in.** If someone tries to log in before verifying, `POST /api/auth/sign-in/email` rejects it with HTTP 403 and the error code `EMAIL_NOT_VERIFIED`. The login page recognizes that specific code and shows a message saying the email isn't verified yet, along with its own resend button. Once verified, a normal login succeeds and sets the session cookie.
8. **Existing users.** Anyone who had an account before this feature existed was marked verified by a one-time migration, so enforcement didn't lock them out.

So in short: create the account → they get an email → they can't log in until they click the link → the link flips the flag and sends them to the app → login then works. If they lose the email, both the register and login pages have a resend button.

---

## 2. Where the pieces live

| Concern | File | Key symbol |
|---|---|---|
| Register UI (form + confirmation card + `?email=` prefill) | `delivery/webapp/src/app/register/page.tsx` | `RegisterPage`, `handleSubmit` |
| Login UI (unverified handling + resend) | `delivery/webapp/src/app/login/page.tsx` | `LoginPage`, `handleSubmit` |
| Shared resend button behaviour | `delivery/webapp/src/hooks/useResendVerification.ts` | `useResendVerification` |
| Client auth methods | `delivery/webapp/src/lib/auth-client.ts` | `signUp`, `requestVerificationEmail` |
| Server auth config (the decisions) | `delivery/webapp/src/lib/auth.ts` | `auth`, `requireEmailVerification` |
| Transactional email | `delivery/webapp/src/lib/email/client.ts` | `sendVerificationEmail` |
| Lead-capture email (Brevo, `Notify me`) | `delivery/webapp/src/lib/brevo/client.ts`, `delivery/webapp/src/app/api/capture-email/route.ts` | `upsertContact`, `sendConfirmationEmail` |
| HTTP endpoint that Better Auth serves | `delivery/webapp/src/app/api/auth/[...all]/route.ts` | `GET`, `POST` |
| Auth gate / middleware | `delivery/webapp/src/proxy.ts` | `proxy`, `PUBLIC_PATHS` |
| Database tables | `delivery/webapp/src/db/schema.ts` | `users`, `accounts`, `sessions`, `verifications` |
| One-time backfill for existing users | `delivery/webapp/drizzle/0023_backfill_email_verified.sql` | — |
| Shared validation rules | `delivery/webapp/src/lib/validation.ts` | `isValidEmail`, `MIN_PASSWORD_LENGTH` |
| Copy, both locales | `delivery/webapp/src/lib/i18n/messages/core.ts` | `auth.register.*`, `auth.signIn.unverified.*` |
| Tests | `delivery/webapp/src/test/auth/register.test.tsx`, `delivery/webapp/src/test/auth/login.test.tsx` | — |

The single most important idea to hold on to: **our code does not implement email verification.** Better Auth implements it. Our repository only (a) turns it on in `delivery/webapp/src/lib/auth.ts`, (b) supplies a function that delivers the email, and (c) renders UI around the two outcomes — "signed up, not yet verified" and "tried to log in, not yet verified".

---

## 3. Turning it on — `delivery/webapp/src/lib/auth.ts`

Everything starts here. A Better Auth instance is created once and exported; the Next.js route handler and the middleware both import it.

```ts title="delivery/webapp/src/lib/auth.ts"
import { betterAuth } from "better-auth";
import { drizzleAdapter } from "better-auth/adapters/drizzle";
import { nextCookies } from "better-auth/next-js";
import { db } from "@/db";
import * as schema from "@/db/schema";
import { sendPasswordResetEmail, sendVerificationEmail } from "@/lib/email/client";

export const auth = betterAuth({
  database: drizzleAdapter(db, {
    provider: "pg",
    schema: {
      user: schema.users,
      account: schema.accounts,
      session: schema.sessions,
      verification: schema.verifications,
    },
    usePlural: false,
  }),
```

The `drizzleAdapter` binding maps Better Auth's four built-in models onto our Drizzle table definitions. `usePlural: false` matters: our tables are named `user`, `account`, `session`, `verification` (singular), matching Better Auth's defaults.

The configuration that defines sign-up behaviour is the `emailAndPassword` and `emailVerification` blocks:

```ts title="delivery/webapp/src/lib/auth.ts"
  emailAndPassword: {
    enabled: true,
    maxPasswordLength: 128,
    // New sign-ups must verify their inbox before signing in (spec v1). The
    // 0023_backfill_email_verified.sql migration marks pre-existing users as
    // verified so enforcement doesn't lock them out.
    requireEmailVerification: true,
    sendResetPassword: async ({ user, url }) =>
      sendPasswordResetEmail({ to: user.email, url }),
  },
  emailVerification: {
    sendVerificationEmail: async ({ user, url }) =>
      sendVerificationEmail({ to: user.email, url }),
  },
```

Three things to notice:

- **`requireEmailVerification: true` is the switch.** It has two effects: sign-up no longer auto-creates a session, and sign-in is blocked for unverified users. Both are discussed in §5 and §9.
- **`sendVerificationEmail` lives under `emailVerification`, not `emailAndPassword`.** This nesting is easy to get wrong; the two blocks are siblings. `sendResetPassword` is the odd one out — it belongs to `emailAndPassword`. Getting the nesting wrong fails at typecheck or silently sends nothing.
- **The option is named `sendVerificationEmail` in both blocks' vocabulary but ours is a thin wrapper** — it receives `{ user, url }` from Better Auth and delegates to our Resend client, passing only the two fields it needs.

Sessions are configured immediately below:

```ts title="delivery/webapp/src/lib/auth.ts"
  plugins: [nextCookies()],
  session: {
    expiresIn: 60 * 60 * 24 * 7, // 7 days
    updateAge: 60 * 60 * 24, // update session every 24 hours
  },
  advanced: {
    useSecureCookies: process.env.NODE_ENV === "production",
    database: {
      generateId: "serial",
    },
  },
});
```

- `nextCookies()` is the plugin that lets Better Auth set cookies from Next.js server actions and route handlers.
- `useSecureCookies` is gated on `NODE_ENV`, so local dev over plain HTTP still works while production gets the `__Secure-` prefixed cookie. This matters in `delivery/webapp/src/proxy.ts`, which has to recognize both cookie names.
- `generateId: "serial"` delegates ID generation to the database, because our Better Auth tables use `BIGINT GENERATED ALWAYS AS IDENTITY` primary keys rather than UUID strings.

### The tables behind it

```ts title="delivery/webapp/src/db/schema.ts"
export const users = pgTable("user", {
  id: bigint("id", { mode: "number" }).generatedAlwaysAsIdentity().primaryKey(),
  name: text("name").notNull(),
  email: text("email").notNull().unique(),
  emailVerified: boolean("emailVerified").notNull().default(false),
  image: text("image"),
  createdAt: timestamp("createdAt", { withTimezone: true }).notNull().defaultNow(),
  updatedAt: timestamp("updatedAt", { withTimezone: true }).notNull().defaultNow(),
});
```

`emailVerified` defaults to `false` and the `email` column is `UNIQUE` — that uniqueness is what makes duplicate detection possible at sign-up. Column names are deliberately camelCase to match Better Auth's expectations.

There is also a `verification` table:

```ts title="delivery/webapp/src/db/schema.ts"
export const verifications = pgTable("verification", {
  id: bigint("id", { mode: "number" }).generatedAlwaysAsIdentity().primaryKey(),
  identifier: text("identifier").notNull(),
  value: text("value").notNull(),
  expiresAt: timestamp("expiresAt", { withTimezone: true }).notNull(),
  createdAt: timestamp("createdAt", { withTimezone: true }).notNull().defaultNow(),
  updatedAt: timestamp("updatedAt", { withTimezone: true }).notNull().defaultNow(),
});
```

**A common misconception worth heading off:** this table is not used by email verification. Email verification tokens are stateless JWTs signed with `BETTER_AUTH_SECRET` — nothing is written to the database when a verification email is sent, so there is no row to expire or clean up. The `verification` table stores stateful tokens for other flows, notably password reset (rows identified as `reset-password:<token>`). If you are debugging a verification link and go looking in this table, you will find nothing.

### The email delivery layer

```ts title="delivery/webapp/src/lib/email/client.ts"
import { Resend } from "resend";

// Thin Resend wrapper for transactional email (spec:
// specs/webapp-email-validation-password-reset-v1.md). No-op-safe: without
// RESEND_API_KEY (local dev) sends are logged and skipped — the webapp keeps
// working; production requires the key.

interface EmailSendArgs {
  to: string;
  url: string;
}

function getResend(): Resend | null {
  const apiKey = process.env.RESEND_API_KEY;
  if (!apiKey) return null;
  return new Resend(apiKey);
}
```

The wrapper is deliberately defensive. When no API key is configured it logs and returns rather than throwing:

```ts title="delivery/webapp/src/lib/email/client.ts"
async function sendEmail({
  to,
  subject,
  html,
  skipLabel,
  errorLabel,
}: SendEmailParams): Promise<void> {
  const resend = getResend();
  if (!resend) {
    console.warn(`[email] RESEND_API_KEY not set; skipping ${skipLabel} to ${to}`);
    return;
  }
  const { error } = await resend.emails.send({
    from: fromAddress(),
    to,
    subject,
    html,
  });
  if (error) console.error(`[email] ${errorLabel}:`, error);
}
```

**This is the single biggest gotcha when testing locally.** With `RESEND_API_KEY` unset, sign-up still succeeds, the confirmation screen still appears, and the API still returns success — but no email is ever delivered. It is not broken; it is skipping. Set the key (see `delivery/webapp/.env.example`):

```bash title="delivery/webapp/.env.example"
# Resend transactional email (verification + password reset).
# Leave RESEND_API_KEY unset for local dev — the email client logs and skips.
RESEND_API_KEY=
RESEND_FROM_ADDRESS=Stream of Worship <noreply@streamofworship.com>
```

Note also that a Resend API error is **logged, not propagated**. From the caller's perspective, sending always "succeeds". A misconfigured `RESEND_FROM_ADDRESS` therefore shows up as silence plus a server log line, not as a user-facing error. In production the key is required; `delivery/webapp/.env.production.example` says so explicitly.

The verification message itself:

```ts title="delivery/webapp/src/lib/email/client.ts"
/**
 * Send the email-verification message. `url` points at Better Auth's
 * `/verify-email?token=...&callbackURL=...` endpoint (auto sign-in + redirect
 * handled server-side).
 */
export async function sendVerificationEmail({
  to,
  url,
}: EmailSendArgs): Promise<void> {
  await sendEmail({
    to,
    subject: "Verify your email",
    html: `<p>Welcome to Stream of Worship!</p><p><a href="${url}">Verify your email</a></p><p>If you didn't create an account, you can safely ignore this email.</p>`,
    skipLabel: "verification email",
    errorLabel: "Failed to send verification email",
  });
}
```

Our function's job is only to build the HTML around the `url` Better Auth gave us. It does not construct the URL, generate the token, or decide the redirect — all of that is Better Auth's.

### The HTTP surface

Better Auth registers a large set of endpoints; we expose them with a catch-all route:

```ts title="delivery/webapp/src/app/api/auth/[...all]/route.ts"
import { toNextJsHandler } from "better-auth/next-js";
import { auth } from "@/lib/auth";

export const { GET, POST } = toNextJsHandler(auth.handler);
```

There is no per-endpoint file. `POST /api/auth/sign-up/email`, `POST /api/auth/sign-in/email`, `GET /api/auth/verify-email`, and `POST /api/auth/send-verification-email` are all served by this one file. If you add a new auth-related call, you usually do not touch this file at all.

---

## 4. Step 1 — the register form

`delivery/webapp/src/app/register/page.tsx` is a client component. It holds four field values (`name`, `email`, `password`, `confirmPassword`), an `errors` object, and a `loading` flag — all plain `useState`. The post-submit screen is driven by two more:

```ts title="delivery/webapp/src/app/register/page.tsx"
  const [loading, setLoading] = useState(false);
  // With requireEmailVerification, signUp.email() no longer auto-signs-in.
  // On success we swap to a "check your email" confirmation state instead of
  // navigating (spec v1, Phase 4).
  const [submittedEmail, setSubmittedEmail] = useState<string | null>(null);
  const { resending, resendState, resend } = useResendVerification(submittedEmail);
```

`submittedEmail` is doing double duty: it is both the flag that switches the render from form to confirmation card, and the value interpolated into the confirmation copy. That is why the hook is initialised with it — the resend button needs an address, and before submission there isn't one.

### Prefill from a confirmation link

The marketing site's "Notify me" form emails a lead an email-validation link (`/validated?token=…`, built by `delivery/webapp/src/app/api/capture-email/route.ts`). Visiting that link marks the lead's Brevo contact as `VALIDATED` and offers a "Create your free account" CTA that carries the address across: `/register?email=…`. The register page reads that param once on mount:

```ts title="delivery/webapp/src/app/register/page.tsx"
  // Prefill from ?email= (confirmation-email deep link) exactly once on mount.
  // Never clobber: only fill while the field is empty, so anything the user
  // already typed wins. Read via window.location, not useSearchParams, to
  // avoid a Suspense boundary around the page.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const urlEmail = params.get("email");
    if (urlEmail) {
      setEmail((current) => (current ? current : urlEmail));
    }
  }, []);
```

Two details are deliberate. The functional `setEmail` update is what makes the prefill non-destructive — it decides against the *current* value, so a fast typist never loses input to the effect. And the param is read from `window.location` rather than `useSearchParams`, which keeps the page free of a Suspense boundary; the mount-only dependency array means a later URL change cannot retroactively overwrite an edited field.

### Client-side validation

```ts title="delivery/webapp/src/app/register/page.tsx"
  function validate() {
    const next: typeof errors = {};
    if (!name) {
      next.name = t("auth.register.validation.nameRequired");
    }
    if (!email) {
      next.email = t("auth.register.validation.emailRequired");
    } else if (!isValidEmail(email)) {
      next.email = t("auth.register.validation.emailFormat");
    }
    if (!password) {
      next.password = t("auth.register.validation.passwordRequired");
    } else if (password.length < MIN_PASSWORD_LENGTH) {
      next.password = t("auth.register.validation.passwordShort");
    }
    if (!confirmPassword) {
      next.confirmPassword = t("auth.register.validation.confirmRequired");
    } else if (confirmPassword !== password) {
      next.confirmPassword = t("auth.register.validation.confirmMismatch");
    }
    return next;
  }
```

The shared constants come from `delivery/webapp/src/lib/validation.ts`:

```ts title="delivery/webapp/src/lib/validation.ts"
export const MIN_PASSWORD_LENGTH = 8;

export function isValidEmail(email: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
}
```

This validation is a **UX affordance, not a security boundary** — it exists to give fast feedback without a round trip. The real rules are enforced again server-side by Better Auth (`z.email()`, plus `minPasswordLength` and `maxPasswordLength: 128` from `delivery/webapp/src/lib/auth.ts`). If you change a rule here, change it there too, or the two will disagree.

Note the error keys are i18n keys, not strings. Every user-visible message in this flow is a key resolved through `t()` against `delivery/webapp/src/lib/i18n/messages/core.ts`, which carries both `en` and `zh-Hant` entries.

### Submission

```ts title="delivery/webapp/src/app/register/page.tsx"
  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const validation = validate();
    if (Object.keys(validation).length > 0) {
      setErrors(validation);
      return;
    }
    setErrors({});
    setLoading(true);
    try {
      const result = await signUp.email({ email, password, name });
      if (result.error) {
        setErrors({ form: result.error.message ?? t("auth.register.error.failed") });
      } else {
        // Persist the chosen display locale to user_settings so the post-login
        // UI is in the selected language. Best-effort: the confirmation screen
        // renders even if this fails (defaults to en).
        await persistLocale(locale);
        setSubmittedEmail(email);
      }
    } catch {
      setErrors({ form: t("auth.register.error.unexpected") });
    } finally {
      setLoading(false);
    }
  }
```

The important line is the success branch: **there is no `router.push`**. On success the component just records the email and re-renders. This is the visible consequence of `requireEmailVerification` — the old behaviour of auto-signing-in and navigating to the dashboard is gone.

`persistLocale(locale)` is a best-effort `PUT /api/settings` (see `delivery/webapp/src/lib/persist-locale.ts`). At this moment the user has no session, so that request returns 401 and the call is a no-op. The display-language choice made before submitting is actually carried by the `sow_locale` cookie, which `LanguageSwitcher` writes and `resolveUserLocale()` reads. Treat the `persistLocale` call here as harmless, not load-bearing.

`signUp` is re-exported from the shared client:

```ts title="delivery/webapp/src/lib/auth-client.ts"
import { createAuthClient } from "better-auth/react";

export const authClient = createAuthClient({
  // No baseURL — uses current browser origin, works on any host/port
});

export const {
  signIn,
  signOut,
  signUp,
  useSession,
  requestPasswordReset,
  resetPassword,
  changePassword,
  updateUser,
  sendVerificationEmail: requestVerificationEmail,
} = authClient;
```

Better Auth calls this client API `sendVerificationEmail`; our code aliases it to `requestVerificationEmail` on export, because "request" describes what the caller does (ask for a new email) while `send...` sounds like it performs the send. Both names appear in this codebase — the alias is why the register page imports `requestVerificationEmail` but `delivery/webapp/src/lib/auth.ts` configures `sendVerificationEmail`.

### Testing sign-up without an email provider

Because `sendVerificationEmail` is skipped without an API key, the fastest way to drive the flow locally is to read the token out of the generated URL rather than your inbox. You can exercise the whole thing against the in-memory adapter in a throwaway script — build a `betterAuth()` instance with the same option shape as `delivery/webapp/src/lib/auth.ts`, pass a `sendVerificationEmail` that pushes `url` into an array, and call `auth.handler(new Request(...))` directly. That is how the behaviours in §5–§9 were confirmed while writing this document. Two details that will otherwise bite you: the memory adapter must be seeded with the expected tables

```ts
database: memoryAdapter({ user: [], session: [], account: [], verification: [] }),
```

and requests must carry an `origin` header, or Better Auth's origin check rejects them.

---

## 5. Step 2 — what the server does on sign-up

Three things happen inside Better Auth's `/sign-up/email` handler, in this order.

### It decides whether to create a session

```ts title="delivery/webapp/node_modules/better-auth/dist/api/routes/sign-up.mjs"
		const shouldReturnGenericDuplicateResponse = ctx.context.options.emailAndPassword.requireEmailVerification || ctx.context.options.emailAndPassword.autoSignIn === false;
		const shouldSkipAutoSignIn = ctx.context.options.emailAndPassword.autoSignIn === false || shouldReturnGenericDuplicateResponse;
```

`shouldSkipAutoSignIn` is true purely because we set `requireEmailVerification: true`. That is the whole reason the register page has to show a confirmation screen instead of navigating: no session cookie is issued.

### It creates the user, then conditionally sends the email

```ts title="delivery/webapp/node_modules/better-auth/dist/api/routes/sign-up.mjs"
		if (ctx.context.options.emailVerification?.sendOnSignUp ?? ctx.context.options.emailAndPassword.requireEmailVerification) {
			const token = await createEmailVerificationToken(ctx.context.secret, createdUser.email, void 0, ctx.context.options.emailVerification?.expiresIn);
			const callbackURL = body.callbackURL ? encodeURIComponent(body.callbackURL) : encodeURIComponent("/");
			const url = `${ctx.context.baseURL}/verify-email?token=${token}&callbackURL=${callbackURL}`;
			if (ctx.context.options.emailVerification?.sendVerificationEmail) await ctx.context.runInBackgroundOrAwait(ctx.context.options.emailVerification.sendVerificationEmail({
				user: createdUser,
				url,
				token
			}, ctx.request));
		}
```

This line explains our configuration choices. The send condition is `sendOnSignUp ?? requireEmailVerification` — we never set `sendOnSignUp`, so it falls through to `requireEmailVerification`, which is `true`. That is why the email is sent on sign-up without any extra flag.

It also explains why the `url` in our mailer looks the way it does: it is `${ctx.context.baseURL}/verify-email?token=...&callbackURL=...`, where `baseURL` comes from `BETTER_AUTH_URL` (or the request origin). The token is created with `createEmailVerificationToken`, which defaults to `expiresIn = 3600` seconds — one hour. We do not override `emailVerification.expiresIn`, so one hour it is.

Note the URL path: it points at `/api/auth/verify-email`, i.e. straight at the catch-all handler from §3. Clicking the link does not land on a page in our app.

Note also that `callbackURL` defaults to `%2F` (a URL-encoded `/`) when the caller doesn't supply one. Our register page does not pass a `callbackURL` to `signUp.email`, so the post-verification destination is always the root. The resend path (§8) explicitly passes `callbackURL: "/"`, which produces the same result.

Finally, `runInBackgroundOrAwait` does not queue the send in production here — with no `advanced.backgroundTasks.handler` configured, it simply awaits the promise. The email is sent inline before the response returns.

### It returns a session-less success

The response is `200` with `token: null` and a user object whose `emailVerified` is `false`. There is no `Set-Cookie` header. This is the concrete meaning of "created an account but not signed in", and it is what the register page's `result.error` check keys off — a successful response is not an error, it is simply a success without a session.

### Duplicate emails are deliberately not an error

With `requireEmailVerification` on, signing up with an address that already has an account returns a **synthetic** user — a `200` response containing a freshly generated random ID and a name echo — rather than a 422 "user already exists":

```ts title="delivery/webapp/node_modules/better-auth/dist/api/routes/sign-up.mjs"
			if (shouldReturnGenericDuplicateResponse) {
				/**
				* Hash the password to reduce timing differences
				* between existing and non-existing emails.
				*/
				await ctx.context.password.hash(password);
```

The password is hashed and discarded, purely to make the two paths take similar time. No user is created, no email is sent, and no `onExistingUserSignUp` callback is configured in our `delivery/webapp/src/lib/auth.ts`, so nothing notifies the existing account holder either.

Two consequences a new developer should be aware of:

- The register page cannot distinguish "new account" from "that email is already taken" — both look like success, so both render "Check your email". This is intentional anti-enumeration behaviour, not a bug to fix by reading the response more carefully.
- If a real user forgets they already registered and tries again, they get the confirmation screen and no email. The recovery path is the resend button (which also sends nothing, since the account is already verified) or signing in / using forgot-password. The tests encode the generic response as the expected contract:

```tsx title="delivery/webapp/src/test/auth/register.test.tsx"
  it("shows error on duplicate email", async () => {
    mockSignUp.mockResolvedValue({
      data: null,
      error: { message: "User already exists" },
    });
```

Note this test mocks the *rejection* shape — it pins the UI's handling of an error response, not the synthetic-success behaviour of the live endpoint. If you change the UI's success path, this test will not catch a mismatch with the server.

---

## 6. Step 3 — the confirmation screen

When `submittedEmail` is set, the component returns early:

```tsx title="delivery/webapp/src/app/register/page.tsx"
  if (submittedEmail) {
    return (
      <div className="min-h-screen flex items-center justify-center p-4">
        <Card className="w-full max-w-sm">
          <CardHeader className="space-y-1">
            <CardTitle className="text-2xl">{t("auth.register.verify.title")}</CardTitle>
            <CardDescription>
              {t("auth.register.verify.subtitle").replace("${email}", submittedEmail)}
            </CardDescription>
          </CardHeader>
```

The copy comes from the locale files, with the address substituted by a plain string replace rather than a template function:

```ts title="delivery/webapp/src/lib/i18n/messages/core.ts"
    "auth.register.verify.title": "Check your email",
    "auth.register.verify.subtitle": "We sent a verification link to ${email}. Click it to finish creating your account.",
    "auth.register.verify.resend": "Resend verification email",
    "auth.register.verify.resending": "Resending...",
    "auth.register.verify.resendSent": "Verification email sent",
    "auth.register.verify.resendError": "Couldn't resend. Please try again.",
```

The button and its feedback states:

```tsx title="delivery/webapp/src/app/register/page.tsx"
            <Button
              type="button"
              variant="outline"
              className="w-full"
              onClick={resend}
              disabled={resending}
            >
              {resending ? t("auth.register.verify.resending") : t("auth.register.verify.resend")}
            </Button>
            {resendState === "sent" && (
              <p className="text-sm text-muted-foreground text-center" role="status">
                {t("auth.register.verify.resendSent")}
              </p>
            )}
            {resendState === "error" && (
              <p className="text-sm text-destructive text-center" role="alert">
                {t("auth.register.verify.resendError")}
              </p>
            )}
```

Note the differing ARIA roles: `role="status"` for success and `role="alert"` for failure, so screen readers announce only the errors assertively. Below the button is a link back to `/login` for the case where the user already has an account.

---

## 7. Step 4 — the resend hook

Both the register and login pages need a resend button. Rather than duplicate the request-and-report logic, it lives in one hook:

```ts title="delivery/webapp/src/hooks/useResendVerification.ts"
/**
 * Shared resend-verification flow for the login and register pages.
 * Centralizes the request call and its idle/sent/error state so both pages
 * render the same confirmation affordance without duplicating logic.
 */
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

The `email: string | null` signature is what lets the hook be mounted unconditionally — on the login page it is `null` until an unverified sign-in reveals the address, and `resend` becomes a no-op until then. The `callbackURL: "/"` is the destination after the user clicks the new link.

The endpoint it calls is deliberately uninformative. From Better Auth:

```ts title="delivery/webapp/node_modules/better-auth/dist/api/routes/email-verification.mjs"
	const session = await getSessionFromCtx(ctx);
	if (!session) {
		const user = await ctx.context.internalAdapter.findUserByEmail(email);
		if (!user || user.user.emailVerified) {
			await createEmailVerificationToken(ctx.context.secret, email, void 0, ctx.context.options.emailVerification?.expiresIn);
			return ctx.json({ status: true });
		}
		await sendVerificationEmailFn(ctx, user.user);
		return ctx.json({ status: true });
	}
```

Read this carefully, because it explains why the resend button lies — in a good way. When the request is unauthenticated, the server returns `{ status: true }` **unconditionally**. It only actually sends mail when the user exists and is unverified. For an unknown address, or an already-verified one, it burns a token and returns success without sending anything.

So `resendState === "sent"` means "the request was accepted", not "an email was delivered". The UI cannot know the difference, and this is intentional: if the endpoint distinguished the cases, it would be an account-enumeration oracle. The same reasoning as §5's synthetic duplicate response.

Also note the signed-in branch is not reachable from our UI: if a session exists and the address doesn't match, Better Auth throws `EMAIL_MISMATCH`; if it matches and is verified, it throws `EMAIL_ALREADY_VERIFIED`. Our resend buttons only ever run while there is no session — the register page has none yet, and the login page has none because sign-in just failed.

---

## 8. Step 5 — clicking the verification link

The email link is `GET /api/auth/verify-email?token=...&callbackURL=%2F`, served by the catch-all route. Better Auth validates the token and, on success, marks the user verified:

```ts title="delivery/webapp/node_modules/better-auth/dist/api/routes/email-verification.mjs"
	if (user.user.emailVerified) {
		if (ctx.query.callbackURL) throw ctx.redirect(ctx.query.callbackURL);
		return ctx.json({
			status: true,
			user: null
		});
	}
	if (ctx.context.options.emailVerification?.beforeEmailVerification) await ctx.context.options.emailVerification.beforeEmailVerification(user.user, ctx.request);
	const updatedUser = await ctx.context.internalAdapter.updateUserByEmail(parsed.email, { emailVerified: true });
```

The guard at the top makes the endpoint idempotent: an already-verified user just gets redirected, so a second click on the same link is harmless and cannot fail. We configure neither `beforeEmailVerification` nor `afterEmailVerification`, so those hooks are no-ops.

The token itself is a stateless JWT. It is verified with the auth secret and carries an `exp` claim one hour out; nothing about it is stored server-side, which is why the `verification` table stays empty (§3).

### What happens next, precisely

After marking the user verified, Better Auth redirects to the `callbackURL` — `/` in our case. Whether a session is created at this point depends on one option:

```ts title="delivery/webapp/node_modules/better-auth/dist/api/routes/email-verification.mjs"
	if (ctx.context.options.emailVerification?.autoSignInAfterVerification) {
		const currentSession = await getSessionFromCtx(ctx);
		if (!currentSession || currentSession.user.email !== parsed.email) {
```

**Our `delivery/webapp/src/lib/auth.ts` does not set `autoSignInAfterVerification`, and Better Auth has no default for it** — the option must be explicitly `true` to take effect. So today no session cookie is issued by the verification click. The user is redirected to `/`, which is auth-gated:

- `delivery/webapp/src/proxy.ts` treats `/` as non-public (the marketing site lives on a separate domain), so a request with no session cookie is redirected to `/login?callbackUrl=/`.

The practical result is that a newly verified user lands on the login page and signs in once with the password they just chose. If you want verification to sign the user in directly — which is what `specs/webapp-email-validation-password-reset-v1.md` describes under "Auto sign-in after verification" — that is a one-line addition to the `emailVerification` block in `delivery/webapp/src/lib/auth.ts`. As of this writing it is not there, so the documented flow and the shipped behaviour differ on this point.

The reason for the mismatch is instructive if you are tempted to trust a spec over the code: the spec's Phase 2 implementation note asserted that "`autoSignInAfterVerification` stays default `true`", so it prescribed no explicit setting. That assumption is wrong for the version installed here — there is no default, and the option is only honoured when it is truthy. The safe habit is to confirm behaviour against `delivery/webapp/node_modules/better-auth/` (or a throwaway harness, §4) rather than against the prose, for any option you have not set explicitly.

### Expired or tampered links

An invalid token does not produce a friendly page. Better Auth redirects with an error code appended to the callback URL:

```ts title="delivery/webapp/node_modules/better-auth/dist/api/routes/email-verification.mjs"
	function redirectOnError(error) {
		if (ctx.query.callbackURL) {
			if (ctx.query.callbackURL.includes("?")) throw ctx.redirect(`${ctx.query.callbackURL}&error=${error.code}`);
			throw ctx.redirect(`${ctx.query.callbackURL}?error=${error.code}`);
		}
		throw APIError.from("UNAUTHORIZED", error);
	}
```

With our `callbackURL=/`, an expired link sends the user to `/?error=TOKEN_EXPIRED`, and a bad one to `/?error=INVALID_TOKEN`. **Nothing in the app reads those parameters.** `delivery/webapp/src/app/reset-password/page.tsx` does read an `error` query param for the reset flow, but no equivalent exists on the home page. So a user with a stale link is silently bounced to `/login` with no explanation of why their link didn't work; they have to notice the missing verification and use a resend button themselves. If you are adding polish to this flow, this is the highest-value place to put it.

---

## 9. Step 6 — the login gate

Unauthenticated sign-in with an unverified account is rejected:

```ts title="delivery/webapp/node_modules/better-auth/dist/api/routes/sign-in.mjs"
	if (ctx.context.options?.emailAndPassword?.requireEmailVerification && !user.user.emailVerified) {
		if (!ctx.context.options?.emailVerification?.sendVerificationEmail) throw APIError.from("FORBIDDEN", BASE_ERROR_CODES.EMAIL_NOT_VERIFIED);
		if (ctx.context.options?.emailVerification?.sendOnSignIn) {
			const token = await createEmailVerificationToken(ctx.context.secret, user.user.email, void 0, ctx.context.options.emailVerification?.expiresIn);
			const callbackURL = ctx.body.callbackURL ? encodeURIComponent(ctx.body.callbackURL) : encodeURIComponent("/");
			const url = `${ctx.context.baseURL}/verify-email?token=${token}&callbackURL=${callbackURL}`;
			await ctx.context.runInBackgroundOrAwait(ctx.context.options.emailVerification.sendVerificationEmail({
				user: user.user,
				url,
				token
			}, ctx.request));
		}
		throw APIError.from("FORBIDDEN", BASE_ERROR_CODES.EMAIL_NOT_VERIFIED);
	}
```

The rejection is HTTP 403 with code `EMAIL_NOT_VERIFIED`, and it is thrown **after** the password has already been verified. That ordering is deliberate: the server confirms the credentials are right before revealing that the address is unverified.

Two details about the inner block. We do not set `sendOnSignIn` (its default is `false`), so a failed login never auto-sends a fresh email — the user must press resend. And because `sendVerificationEmail` *is* configured, the guard above it doesn't fire, so the unverified case is a clean 403 rather than a "verification not enabled" misconfiguration error.

The login page keys off the error code specifically:

```ts title="delivery/webapp/src/app/login/page.tsx"
      const result = await signIn.email({ email, password });
      if (result.error) {
        // Better Auth returns 403 + code EMAIL_NOT_VERIFIED when the account
        // is unverified — surface a resend action instead of a dead-end error.
        if (result.error.code === "EMAIL_NOT_VERIFIED") {
          setUnverifiedEmail(email);
          setErrors({});
        } else {
          setErrors({ form: result.error.message ?? t("auth.signIn.error.invalid") });
        }
```

Matching on `code` rather than on `status === 403` or on the message text is the right level of coupling: the code is a stable identifier, whereas the human-readable message is not. Anything that is *not* this code falls through to the ordinary error path, which the tests pin explicitly:

```tsx title="delivery/webapp/src/test/auth/login.test.tsx"
  it("keeps the form error for non-verification sign-in failures", async () => {
    mockSignIn.mockResolvedValue({
      data: null,
      error: { message: "Invalid email or password", code: "INVALID_EMAIL_OR_PASSWORD" },
    });
```

On the success path, note the two things that happen before navigation:

```ts title="delivery/webapp/src/app/login/page.tsx"
        await persistLocale(locale);
        // Honor deep-link callbackUrl from proxy.ts, but never open-redirect
        // (external/protocol-relative URLs) or loop back to auth pages.
        const callbackUrl = new URLSearchParams(window.location.search).get("callbackUrl");
        const safeCallback =
          callbackUrl &&
          callbackUrl.startsWith("/") &&
          !callbackUrl.startsWith("//") &&
          callbackUrl !== "/login" &&
          callbackUrl !== "/register"
            ? callbackUrl
            : "/";
        router.push(safeCallback);
        router.refresh();
```

Unlike the register page, this `persistLocale` call is meaningful — there is now a session, so `PUT /api/settings` succeeds. The `safeCallback` checks are the open-redirect guard: a callback URL must be a same-site absolute path (`/...`), must not be protocol-relative (`//evil.com`), and must not point back at an auth page (which would loop). This matters because `delivery/webapp/src/proxy.ts` writes `callbackUrl` into the login URL when it redirects a protected page, so the value is attacker-influenceable.

### The unverified UI

```tsx title="delivery/webapp/src/app/login/page.tsx"
            {unverifiedEmail && (
              <div className="text-sm">
                <p className="text-destructive" role="alert">
                  {t("auth.signIn.unverified.message")}
                </p>
                <Button
                  type="button"
                  variant="link"
                  className="px-0 h-auto"
                  onClick={resend}
                  disabled={resending}
                >
```

`unverifiedEmail` doubles as the flag and the address for the resend hook, exactly as `submittedEmail` does on the register page. The copy:

```ts title="delivery/webapp/src/lib/i18n/messages/core.ts"
    "auth.signIn.unverified.message": "Your email hasn't been verified yet. Check your inbox, or resend the verification email:",
    "auth.signIn.unverified.resend": "Resend verification email",
    "auth.signIn.unverified.resending": "Resending...",
    "auth.signIn.unverified.resendSent": "Verification email sent",
    "auth.signIn.unverified.resendError": "Couldn't resend. Please try again.",
```

---

## 10. The middleware gate

`delivery/webapp/src/proxy.ts` is the Next.js 16 middleware. It is not part of sign-up per se, but it is why the unauthenticated post-verification redirect lands on `/login`:

```ts title="delivery/webapp/src/proxy.ts"
const PUBLIC_PATHS = ["/login", "/register", "/forgot-password", "/reset-password", "/api/auth", "/api/health", "/share", "/api/share", "/sw.js", "/sw-artifact-serving.js"];
```

Both `/register` and `/login` are public, and so is the entire `/api/auth` subtree — without that entry, the sign-up POST and the verification GET would be redirected to the login page and the flow would never work. The comment block above this list explains that `/` is deliberately *not* public, and `delivery/webapp/src/app/page.tsx` repeats the check server-side as a belt-and-braces measure.

Session detection here is a cheap cookie presence check rather than a database lookup:

```ts title="delivery/webapp/src/proxy.ts"
const SESSION_COOKIE_NAMES = [
  "better-auth.session_token",
  "__Secure-better-auth.session_token",
];

function hasSessionCookie(req: NextRequest): boolean {
  return SESSION_COOKIE_NAMES.some((name) => req.cookies.get(name) != null);
}
```

Both names are listed because `useSecureCookies` is gated on `NODE_ENV` in `delivery/webapp/src/lib/auth.ts` — dev and production use different cookie names, and the middleware must recognize either.

---

## 11. The one-time backfill

Enabling `requireEmailVerification` on a live database would lock out every existing account, since all of them have `emailVerified = false`. Migration `0023` prevents that:

```sql title="delivery/webapp/drizzle/0023_backfill_email_verified.sql"
-- Migration: 0023_backfill_email_verified
-- Description: Mark all pre-existing users as email-verified
-- See specs/webapp-email-validation-password-reset-v1.md
--
-- Rationale: existing users already proved ownership by using the app;
-- enforcing email verification must not lock them out. drizzle-kit cannot
-- express "set all rows to true" cleanly, so this is hand-written (same
-- precedent as 0018_theme_anchors.sql).

UPDATE "user" SET "emailVerified" = true WHERE "emailVerified" = false;
```

The quoted identifiers are required because the columns are camelCase, and the `WHERE` clause is redundant (all rows are `false` at that point) but keeps the statement idempotent and explicit. The rationale — existing users already proved ownership by using the app — is the reason the rule is "verified on sign-up, not verified on migration".

---

## 12. Tests, and what they do and don't cover

`delivery/webapp/src/test/auth/register.test.tsx` and `delivery/webapp/src/test/auth/login.test.tsx` mock `@/lib/auth-client`, so they exercise the **page logic** — validation, state transitions, error routing — not the wire protocol or any Better Auth behaviour. That division is worth internalising before you change anything: a green suite here is not evidence that a real sign-up works end to end.

What the register tests pin, in order: rendering all fields; each validation rule (empty name, empty/invalid email, empty/short password, mismatched confirmation); that `signUp.email` is called with the right arguments; that success shows the confirmation card and does **not** navigate; that resend calls the endpoint with `{ email, callbackURL: "/" }`; the error path; the loading state; and locale persistence in both locales.

```tsx title="delivery/webapp/src/test/auth/register.test.tsx"
  it("shows the check-your-email confirmation on success (no redirect)", async () => {
```

The "no redirect" assertion is the one that guards the most important property of this flow, and it is expressed as `expect(mockPush).not.toHaveBeenCalled()`.

The login tests pin the unverified branch specifically, asserting that the resend affordance appears, that navigation does not happen, that the resend call carries the address that failed, and that a non-verification error takes the ordinary path:

```tsx title="delivery/webapp/src/test/auth/login.test.tsx"
  it("shows a resend-verification action for an unverified email", async () => {
    mockSignIn.mockResolvedValue({
      data: null,
      error: { message: "Email not verified", code: "EMAIL_NOT_VERIFIED", status: 403 },
    });
```

**Not covered by unit tests, and therefore worth knowing by hand:**

- Anything server-side. There is no test that asserts `requireEmailVerification` is on, that sign-up returns no cookie, that the token expires in an hour, or that duplicate sign-up is generic. Those live in the library and in `delivery/webapp/src/lib/auth.ts`, and changing the config can regress them silently.
- The verification GET round trip, including the expired-token redirect and its unread `error` parameter (§8).
- The Android client, which talks to the same endpoints. `delivery/android/.../core/network/AuthApi.kt` calls `POST api/auth/sign-up/email` and its `AuthRepository.register` expects the response to yield a signed-in session — worth checking whenever you touch sign-up behaviour, since that client has no `EMAIL_NOT_VERIFIED` branch and no resend affordance.

If you want end-to-end confidence, the realistic path is the throwaway `auth.handler` harness described at the end of §4, driven against a memory adapter, or a real browser against a dev server with a working `RESEND_API_KEY`.

---

## 13. Rules that look arbitrary until you know why

- **Sign-up returns success and no cookie.** Not a bug: `requireEmailVerification` forces `shouldSkipAutoSignIn` (§5).
- **Signing up with an existing email looks like success.** Synthetic-user response, hashed-password timing padding, no email sent (§5). Anti-enumeration.
- **Resend always reports success.** The endpoint returns `{ status: true }` for unknown and already-verified addresses too (§7). Anti-enumeration.
- **The `verification` table is empty after a sign-up.** Verification tokens are stateless JWTs; that table is for password reset and similar flows (§3).
- **The email client never throws on a Resend failure.** Failures are logged; callers always see success (§3). Check server logs, not the UI.
- **Unverified sign-in is checked after the password check, not before.** Credentials are confirmed correct before the unverified state is revealed (§9).
- **No email is sent on a failed sign-in.** `sendOnSignIn` is unset (defaults `false`), so the user must press resend (§9).
- **`/api/auth` is in `PUBLIC_PATHS`.** Without it the sign-up POST and verification GET would be auth-redirected (§10).
- **Existing users were bulk-marked verified.** Otherwise enforcement would have locked out every pre-existing account (§11).
