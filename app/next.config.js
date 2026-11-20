/** @type {import('next').NextConfig} */
const repositoryName = (process.env.GITHUB_REPOSITORY || "").split("/")[1] || "";
const inferredPagesBase = process.env.GITHUB_ACTIONS === "true"
  && repositoryName
  && !repositoryName.endsWith(".github.io")
  ? `/${repositoryName}`
  : "";
const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? inferredPagesBase;
const githubPages = process.env.NEXT_PUBLIC_GITHUB_PAGES === "true";

const withPWA = require("next-pwa")({
  dest: "public",
  // The in-browser AI bundles are large; let the browser cache them itself.
  buildExcludes: [/\.wasm$/, /lend/, /web-llm/, /transformers/, /kokoro/],
  maximumFileSizeToCacheInBytes: 3 * 1024 * 1024,
  disable: process.env.NODE_ENV === "development" || githubPages,
  register: true,
  skipWaiting: true,
});

const nextConfig = {
  reactStrictMode: true,
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  basePath,
  assetPrefix: basePath || undefined,
  webpack: (config) => {
    // transformers.js and kokoro-js run in the browser only.
    config.resolve.alias = {
      ...config.resolve.alias,
      "sharp$": false,
      "onnxruntime-node$": false,
    };
    return config;
  },
  env: {
    NEXT_PUBLIC_BASE_PATH: basePath,
    NEXT_PUBLIC_GITHUB_PAGES: String(githubPages),
  },
};

module.exports = withPWA(nextConfig);
