// @ts-check
import { defineConfig } from 'astro/config';
import react from '@astrojs/react';

// Requests to /api/* from the dev server are forwarded to FastAPI, so the
// browser only ever talks to one origin and no CORS is needed locally.
const backendUrl = process.env.BACKEND_URL ?? 'http://localhost:8000';
// File events don't cross Docker bind mounts reliably on Windows/macOS.
const usePolling = process.env.VITE_USE_POLLING === 'true';

// https://astro.build/config
export default defineConfig({
  integrations: [react()],
  server: { port: 4321 },
  vite: {
    server: {
      watch: usePolling ? { usePolling: true, interval: 300 } : undefined,
      proxy: {
        '/api': { target: backendUrl, changeOrigin: true },
      },
    },
  },
});
