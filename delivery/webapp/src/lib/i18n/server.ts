import { cookies, headers } from "next/headers";
import { auth } from "@/lib/auth";
import { db } from "@/db";
import { userSettings } from "@/db/schema";
import { eq } from "drizzle-orm";
import { isLocale, Locale } from "./messages";

/**
 * Resolve the display language for server-rendered markup (the `<html lang>`
 * attribute and the initial locale passed to the client LocaleProvider).
 *
 * Reads the `sow_locale` cookie (set server-side on settings save) so public
 * no-auth surfaces — share/controller/projection pages — can honor a chosen
 * language. For authenticated requests the saved account setting is
 * authoritative; the cookie only fills the no-auth gap. Falls back to the
 * cookie (or `en`) on any failure so a DB/edge hiccup never blanks the UI.
 */
export async function resolveUserLocale(): Promise<Locale> {
  const cookieLocale = (await cookies()).get("sow_locale")?.value;
  const fallback = (): Locale => (isLocale(cookieLocale) ? cookieLocale : "en");
  try {
    const session = await auth.api.getSession({ headers: await headers() });
    if (!session?.user) return fallback(); // public pages: cookie drives locale

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
