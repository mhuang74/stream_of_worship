import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";

const PUBLIC_PATHS = ["/login", "/register", "/api/auth", "/share", "/api/share"];
// Allow projection pages — matched by suffix to cover both songset and
// share projection routes.
function isPublicPath(pathname: string) {
  if (pathname.endsWith("/play/projection")) return true;
  return PUBLIC_PATHS.some((p) => pathname === p || pathname.startsWith(p + "/"));
}

export async function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;

  if (isPublicPath(pathname)) {
    return NextResponse.next();
  }

  const session = await auth.api.getSession({ headers: request.headers });

  if (!session) {
    // API routes should return a JSON 401, not an HTML redirect. Non-browser
    // clients (Cast receivers, Android app, curl) cannot follow or parse the
    // /login HTML redirect and fail with JSON parse errors like
    // "invalid token '<'". Browser requests still get the redirect for UX.
    if (pathname.startsWith("/api/")) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }
    const loginUrl = new URL("/login", request.url);
    loginUrl.searchParams.set("callbackUrl", pathname);
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)",
  ],
};
