/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  env: {
    F003_LITE_PREVIEW_ENABLED:
      process.env.F003_LITE_PREVIEW_ENABLED ?? "false",
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
