import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  async headers() {
    return [
      {
        source: "/songsets/:id/play/projection",
        headers: [
          {
            key: "Cache-Control",
            value: "no-store, no-cache",
          },
        ],
      },
    ];
  },
};

export default nextConfig;
