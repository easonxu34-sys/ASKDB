import { withAui } from "@assistant-ui/next";
import type { NextConfig } from "next";
import { resolve } from "node:path";

const nextConfig: NextConfig = {
  devIndicators: false,
  turbopack: {
    root: resolve(process.cwd(), ".."),
  },
};

export default withAui(nextConfig);
