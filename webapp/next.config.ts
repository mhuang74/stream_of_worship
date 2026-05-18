import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  allowedDevOrigins: ["192.168.8.102", "100.121.214.94"],
  serverExternalPackages: ["fastembed", "@anush008/tokenizers", "ffmpeg-static"],

  // Image optimization: enable modern formats for any future image usage
  images: {
    formats: ["image/avif", "image/webp"],
    deviceSizes: [390, 768, 1024, 1280, 1920],
    imageSizes: [64, 96, 128, 256],
  },

  // Compress responses for faster transfer
  compress: true,

  async headers() {
    return [
      // Projection pages must never be cached (signed URLs expire)
      {
        source: "/songsets/:id/play/projection",
        headers: [{ key: "Cache-Control", value: "no-store, no-cache" }],
      },
      {
        source: "/share/:token/play/projection",
        headers: [{ key: "Cache-Control", value: "no-store, no-cache" }],
      },
      // Static assets: aggressive caching (Next.js content-hashes them)
      {
        source: "/_next/static/:path*",
        headers: [{ key: "Cache-Control", value: "public, max-age=31536000, immutable" }],
      },
    ];
  },
};

export default nextConfig;
