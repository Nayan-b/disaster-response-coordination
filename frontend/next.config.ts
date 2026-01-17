import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Bundle only the files needed at runtime, so the container image carries a trimmed
  // node_modules instead of the full dependency tree.
  output: "standalone",
};

export default nextConfig;
