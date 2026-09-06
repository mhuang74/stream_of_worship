import type { MetadataRoute } from "next";
import { getSiteBaseUrl } from "@/lib/siteUrl";

/**
 * /robots.txt — allows all crawlers and points at the sitemap so it is
 * discoverable at the conventional location.
 */
export default function robots(): MetadataRoute.Robots {
  const base = getSiteBaseUrl();
  return {
    rules: {
      userAgent: "*",
      allow: "/",
    },
    sitemap: `${base}/sitemap.xml`,
  };
}