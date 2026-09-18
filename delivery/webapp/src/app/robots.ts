import type { MetadataRoute } from "next";

/**
 * /robots.txt — disallow all crawlers. The marketing site
 * (streamofworship.com) owns all public SEO and ships its own robots.txt +
 * sitemap; the app subdomain has no crawlable public content (session-gated
 * pages plus unlisted per-user share links).
 */
export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: "*",
      disallow: "/",
    },
  };
}
