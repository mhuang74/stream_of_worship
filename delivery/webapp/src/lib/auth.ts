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
  trustedOrigins: (request) => {
    if (!request) return [];
    const origin = request.headers.get("origin");
    if (!origin) return [];
    // Allow private/LAN IPs (existing behavior)
    if (/^http:\/\/(192\.168\.|10\.|172\.(1[6-9]|2\d|3[01])\.)/.test(origin)) {
      return [origin];
    }
    // Allow configured origins (e.g. https://unoccluded.tailscale:8080)
    const configured =
      process.env.TRUSTED_ORIGINS?.split(",").map((s) => s.trim()).filter(Boolean) ?? [];
    if (configured.includes(origin)) {
      return [origin];
    }
    return [];
  },
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

export type Session = typeof auth.$Infer.Session;
export type User = typeof auth.$Infer.Session.user;
