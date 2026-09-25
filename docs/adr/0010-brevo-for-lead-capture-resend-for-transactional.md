# Lead capture lives in Brevo; Resend stays transactional-only

The marketing site's email capture flow uses Brevo as the vendor of record for leads: captured email addresses are upserted as Brevo contacts and the confirmation email is sent through Brevo. Resend remains the transactional email vendor for the webapp (signup verification, password reset).

**Leads accumulate in Brevo, which makes this hard to reverse.** Every captured address lands as a Brevo contact — with its list memberships, subscription state, and send history. Exporting the lead store back out and replanting it in another vendor (or the app's own database) means re-creating that state wholesale, and the longer the funnel runs the more history there is to lose. The decision is cheap to make now and expensive to unwind later, which is exactly why it deserves an ADR.

**Two email vendors is surprising, and the trade-off is real.** A reader should not assume one vendor spans both jobs: Brevo owns marketing/lead-capture (contacts + confirmation email), Resend owns the webapp's transactional sends. The cost is two API keys, two sender-verification flows, and two places to check when email misbehaves. The benefit is that each vendor does the job it is built for — Brevo's contact management and list tooling for leads, Resend's transactional deliverability for auth-critical email — without forcing either into a role it handles poorly.

## Considered options

- Single vendor for both jobs (Resend for everything) — rejected: Resend has no equivalent contact/list management, so lead capture would become a hand-rolled store plus raw sends.
- Storing leads in the app's own Postgres instead of Brevo — rejected: duplicates the contact model, adds a retention/compliance surface to the core database, and still needs a marketing sender attached to it.
- A Brevo SDK dependency instead of plain REST — rejected: the integration touches two endpoints (contact upsert, email send); plain `fetch` keeps the dependency tree and update surface minimal, mirroring the Resend wrapper's style.

## Consequences

- `BREVO_API_KEY` is a production secret; unset locally the Brevo wrapper logs and skips (same no-op-safe semantics as the Resend client).
- The confirmation email's signup link derives from the existing `NEXT_PUBLIC_BASE_URL` — no new URL variable. It is never built from the request host: the mail goes from our domain to an address anyone can submit, so a forged `Host` header would otherwise let a third party put their own link in it. Unset falls back to the canonical `https://app.streamofworship.com`.
- The capture endpoint's CORS allowlist comes from `SOW_MARKETING_ORIGINS` (exact match); unset falls back to `https://streamofworship.com` with a warning.
- Email validation gate (bravo_email_flow): the confirmation email links to `/validated?token=…` (HMAC-signed, 48h TTL, keyed on `BETTER_AUTH_SECRET`); the page verifies the token and marks the Brevo contact's `VALIDATED` attribute. Marketing sends filter on `VALIDATED = true`.
- **Prerequisite:** the `VALIDATED` contact attribute must exist in the Brevo account before this flow goes live (Contacts → Settings → Contact attributes, type Boolean, or `POST /v3/contacts/attributes/normal/VALIDATED` with `{"type":"boolean"}`). Brevo silently ignores values for attributes not present in the account, so a missing attribute makes every validation write a silent no-op while the page still reports success.
