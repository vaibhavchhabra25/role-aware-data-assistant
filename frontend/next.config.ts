import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  devIndicators: false, // hide the dev-only toolbar button (errors still show the overlay)
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
