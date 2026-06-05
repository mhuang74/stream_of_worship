import { NextRequest } from "next/server";

export function resolvePublicOrigin(request: NextRequest): string | null {
  const envUrl = process.env.NEXT_PUBLIC_BASE_URL;
  if (envUrl) {
    try {
      const u = new URL(envUrl);
      if (u.origin) return u.origin;
    } catch {}
  }
  if (request.nextUrl?.origin) return request.nextUrl.origin;
  try {
    return new URL(request.url).origin;
  } catch {}
  return null;
}
