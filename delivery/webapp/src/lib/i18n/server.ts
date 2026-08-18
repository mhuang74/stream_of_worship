import { auth } from "@/lib/auth";
import { db } from "@/db";
import { userSettings } from "@/db/schema";
import { eq } from "drizzle-orm";
import { isLocale, Locale } from "./messages";

/**
 * Resolve the authenticated user's display language for server-rendered
 * markup (the `<html lang>` attribute and the initial locale passed to the
 * client LocaleProvider). Returns `en` for unauthenticated requests and falls
 * back to `en` on any failure so a DB/edge hiccup never blanks the UI.
 */
export async function resolveUserLocale(headers: Headers): Promise<Locale> {
  try {
    const session = await auth.api.getSession({ headers });
    if (!session?.user) return "en";

    const userId = Number(session.user.id);
    const rows = await db
      .select({ locale: userSettings.locale })
      .from(userSettings)
      .where(eq(userSettings.userId, userId));

    const value = rows[0]?.locale;
    return isLocale(value) ? value : "en";
  } catch {
    return "en";
  }
}
