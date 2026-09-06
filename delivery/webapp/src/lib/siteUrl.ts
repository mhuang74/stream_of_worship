/**
 * Resolves the site's public base URL for non-request contexts (sitemap.xml,
 * robots.txt) where `resolvePublicOrigin` from share.ts has no request to fall
 * back on. Reads the documented public-base-url env directly and always
 * returns a scheme-qualified origin (no path, no trailing slash).
 */
export function getSiteBaseUrl(): string {
  const envUrl = process.env.NEXT_PUBLIC_BASE_URL;
  if (envUrl) {
    try {
      const u = new URL(envUrl);
      if (u.protocol === "http:" || u.protocol === "https:") {
        // origin strips any path and trailing slash: https://x.com/sub/ -> https://x.com
        return u.origin;
      }
    } catch {}
  }
  // Matches the documented default in .env.example.
  return "http://localhost:8080";
}