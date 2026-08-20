import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { isLocale } from "@/lib/i18n/messages";
import { parseAcceptLanguage } from "@/lib/i18n/accept-language";

const PUBLIC_PATHS = ["/", "/login", "/register", "/api/auth", "/share", "/api/share"];
// Allow projection pages — matched by suffix to cover both songset and
// share projection routes.
function isPublicPath(pathname: string) {
  if (pathname.endsWith("/play/projection")) return true;
  return PUBLIC_PATHS.some((p) => pathname === p || pathname.startsWith(p + "/"));
}

const LOCALE_COOKIE = "sow_locale";

/**
 * Better Auth session cookie names. `useSecureCookies` is gated on
 * NODE_ENV === "production", so the proxy must recognize both forms.
 */
const SESSION_COOKIE_NAMES = [
  "better-auth.session_token",
  "__Secure-better-auth.session_token",
];

function hasSessionCookie(req: NextRequest): boolean {
  return SESSION_COOKIE_NAMES.some((name) => req.cookies.get(name) != null);
}

/**
 * If this is a truly unauthenticated first visit (no sow_locale cookie AND
 * no session cookie), persist the Accept-Language-detected locale so
 * subsequent visits stay consistent. Runs before auth gating; no auth/DB
 * call — only cheap cookie reads. Authenticated users with no sow_locale
 * cookie are skipped: the DB locale is authoritative and the settings PUT
 * syncs the cookie.
 */
function withAutoLocaleCookie(req: NextRequest, res: NextResponse): NextResponse {
  const existing = req.cookies.get(LOCALE_COOKIE)?.value;
  if (existing && isLocale(existing)) return res;
  if (hasSessionCookie(req)) return res;
  const detected = parseAcceptLanguage(req.headers.get("accept-language"));
  res.cookies.set(LOCALE_COOKIE, detected, {
    path: "/",
    maxAge: 60 * 60 * 24 * 365, // 365 days
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
  });
  return res;
}

export async function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;

  if (isPublicPath(pathname)) {
    return withAutoLocaleCookie(request, NextResponse.next());
  }

  const session = await auth.api.getSession({ headers: request.headers });

  if (!session) {
    // API routes should return a JSON 401, not an HTML redirect. Non-browser
    // clients (Cast receivers, Android app, curl) cannot follow or parse the
    // /login HTML redirect and fail with JSON parse errors like
    // "invalid token '<'". Browser requests still get the redirect for UX.
    if (pathname.startsWith("/api/")) {
      return withAutoLocaleCookie(
        request,
        NextResponse.json({ error: "Unauthorized" }, { status: 401 })
      );
    }
    const loginUrl = new URL("/login", request.url);
    loginUrl.searchParams.set("callbackUrl", pathname);
    return withAutoLocaleCookie(request, NextResponse.redirect(loginUrl));
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)",
  ],
};
