import type { MetadataRoute } from "next";
import { SITE_URL } from "@/lib/urls";

export const dynamic = "force-static";

/** Sitemap listing the six marketing pages (en at root, zh-Hant under /zh-Hant). */
export default function sitemap(): MetadataRoute.Sitemap {
  const base = SITE_URL;
  const zh = `${base}/zh-Hant`;
  return [
    { url: `${base}/`, changeFrequency: "weekly", priority: 1 },
    { url: `${base}/about`, changeFrequency: "monthly", priority: 0.5 },
    { url: `${base}/docs`, changeFrequency: "monthly", priority: 0.5 },
    { url: `${zh}/`, changeFrequency: "weekly", priority: 1 },
    { url: `${zh}/about`, changeFrequency: "monthly", priority: 0.5 },
    { url: `${zh}/docs`, changeFrequency: "monthly", priority: 0.5 },
  ];
}
