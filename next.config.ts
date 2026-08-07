import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  images: {
    remotePatterns: [
      { protocol: "https", hostname: "eol.jsc.nasa.gov", pathname: "/DatabaseImages/**" },
    ],
  },
};

export default nextConfig;
