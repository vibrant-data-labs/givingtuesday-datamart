/** @type {import('next').NextConfig} */
const nextConfig = {
  experimental: {
    serverComponentsExternalPackages: ['pg', 'pg-pool', 'pg-protocol', 'pg-types', 'pgpass', 'kysely'],
  },
};

export default nextConfig;
