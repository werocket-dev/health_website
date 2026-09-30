import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: 'standalone',
  experimental: {
    // proxy.ts intercepte aussi /api/* : Next met alors le corps en tampon
    // (10 Mo par défaut, tronqué au-delà). Le zip Breakdance fait plusieurs dizaines de Mo.
    proxyClientMaxBodySize: '200mb',
  },
};

export default nextConfig;
