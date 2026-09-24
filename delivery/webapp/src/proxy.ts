import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { isLocale } from "@/lib/i18n/messages";
import { parseAcceptLanguage } from "@/lib/i18n/accept-language";

const PUBLIC_PATHS = ["/login", "/register", "/forgot-password", "/reset-password", "/api/auth", "/api/health", "/share", "/api/share", "/api/capture-email", "/sw.js", "/sw-artifact-serving.js"];
// "/" is NOT public: the marketing site lives at streamofworship.com (issue
// #213) and unauthenticated visitors to the app domain are login-first —
// they get redirected to /login (proxy + server-render in src/app/page.tsx).
// /api/health stays public: the client's reachability probe (issue #211) must
// never depend on session state, so a stale session cannot produce a false
// "Offline"; see src/hooks/useConnectivity.ts.
// /api/capture-email stays public: its callers are anonymous marketing-site
// visitors (issue #222) who by definition have no session — gating it made the
// funnel return 401 to every real lead (and to the CORS preflight).
// The service worker scripts must stay reachable unauthenticated: they are
// fetched outside the page's own navigation (register() + importScripts), and
// an auth redirect would fail installation with "script resource is behind a
// redirect". Registered here rather than in the matcher's static-asset
// exemption so the rule is exercisable through proxy() (src/test/proxy.test.ts).
// A new importScripts() target must be added here too.
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
    "/((?!_next/static|_next/image|favicon.ico|sitemap\\.xml|robots\\.txt|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)",
  ],
};
