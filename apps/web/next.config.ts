import path from "node:path";
import type { NextConfig } from "next";

// The browser only ever talks to this origin: /api/* is proxied to the API, so the session cookie is first-party
// (httpOnly, SameSite=Lax) and there is no CORS surface. API_URL is read when the app is built.
const apiUrl = process.env.API_URL ?? "http://localhost:8000";
const isProd = process.env.NODE_ENV === "production";

const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=()" },
  ...(isProd
    ? [
        {
          key: "Content-Security-Policy",
          // Next.js emits small inline bootstrap scripts, so script-src needs 'unsafe-inline' without a per-request nonce.
          value: "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
        },
      ]
    : []),
];

const config: NextConfig = {
  output: "standalone",
  // the app is built from its own folder; without this Next may pick up an unrelated lockfile higher up the tree as the project root
  turbopack: { root: path.resolve(import.meta.dirname) },
  reactStrictMode: true,
  poweredByHeader: false,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiUrl}/api/:path*` }];
  },
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default config;
