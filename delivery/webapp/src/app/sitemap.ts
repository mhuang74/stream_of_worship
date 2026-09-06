import type { MetadataRoute } from "next";
import { getSiteBaseUrl } from "@/lib/siteUrl";

/**
 * /sitemap.xml — lists the public pages only. Auth-gated routes (dashboard,
 * /songsets, /favorites, /settings, /share/*, /api/*) are intentionally
 * excluded. lastModified is omitted: pages are localized React renders with
 * no meaningful per-URL last-modified signal.
 */
export default function sitemap(): MetadataRoute.Sitemap {
  const base = getSiteBaseUrl();
  return [
    {
      url: `${base}/`,
      changeFrequency: "weekly",
      priority: 1,
    },
    {
      url: `${base}/about`,
      changeFrequency: "monthly",
      priority: 0.5,
    },
  ];
}