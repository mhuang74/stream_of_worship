import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  allowedDevOrigins: ["192.168.8.102", "100.121.214.94"],
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
