const F003_TRUE_VALUES = new Set(["1", "on", "t", "true", "y", "yes"]);
const F003_FALSE_VALUES = new Set(["0", "off", "f", "false", "n", "no"]);

function normalizedF003LitePreviewFlag(value) {
  if (value === undefined) return "false";

  const normalized = value.toLowerCase();
  if (F003_TRUE_VALUES.has(normalized)) return "true";
  if (F003_FALSE_VALUES.has(normalized)) return "false";

  throw new Error(
    "F003_LITE_PREVIEW_ENABLED must use a Pydantic-supported boolean spelling",
  );
}

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  distDir: process.env.NEXT_DIST_DIR || ".next",
  env: {
    F003_LITE_PREVIEW_ENABLED:
      normalizedF003LitePreviewFlag(process.env.F003_LITE_PREVIEW_ENABLED),
  },
  // Local browser automation blocks cross-origin loopback requests.  Keep the
  // runtime contract unchanged while allowing an explicitly configured dev
  // server to proxy it through the Creator origin.
  async rewrites() {
    const target = process.env.RUNTIME_PROXY_TARGET?.replace(/\/$/, "");
    if (!target) return [];
    return [{ source: "/api/runtime/:path*", destination: `${target}/:path*` }];
  }
};

export default nextConfig;
