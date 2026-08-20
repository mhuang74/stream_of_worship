import { cookies, headers } from "next/headers";
import { auth } from "@/lib/auth";
import { db } from "@/db";
import { userSettings } from "@/db/schema";
import { eq } from "drizzle-orm";
import { isLocale } from "./messages";
import type { Locale } from "./messages";
import { parseAcceptLanguage } from "./accept-language";

/**
 * Resolve the display language for server-rendered markup (the `<html lang>`
 * attribute and the initial locale passed to the client LocaleProvider).
 *
 * Read-only: this function NEVER writes a cookie. Cookie persistence for the
 * Accept-Language-detected locale happens in `src/proxy.ts` (the Next.js 16
 * middleware), where `NextResponse.cookies.set` is valid. On the very first
 * visit the proxy sets the cookie on the response, but Server Components
 * read cookies from the request, so the cookie is absent for that first
 * render — this function falls back to `Accept-Language` so the first render
 * is still correct; subsequent requests see the persisted cookie.
 *
 * Priority: user_settings.locale (authed) → sow_locale cookie →
 * Accept-Language header → `en`.
 */
export async function resolveUserLocale(): Promise<Locale> {
  const cookieLocale = (await cookies()).get("sow_locale")?.value;
  const headerValue = (await headers()).get("accept-language");
  const detected = parseAcceptLanguage(headerValue);

  const fallback = (): Locale => (isLocale(cookieLocale) ? cookieLocale : detected);

  try {
    const session = await auth.api.getSession({ headers: await headers() });
    if (!session?.user) return fallback(); // public pages: cookie/header drives locale

    const userId = Number(session.user.id);
    const rows = await db
      .select({ locale: userSettings.locale })
      .from(userSettings)
      .where(eq(userSettings.userId, userId));

    const value = rows[0]?.locale;
    return isLocale(value) ? value : fallback(); // authenticated: account setting wins
  } catch {
    return fallback();
  }
}
