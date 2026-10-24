// @ts-check
import { defineConfig } from 'astro/config';
import react from '@astrojs/react';

// Requests to /api/* from the dev server are forwarded to FastAPI, so the
// browser only ever talks to one origin and no CORS is needed locally.
const backendUrl = process.env.BACKEND_URL ?? 'http://localhost:8000';

// https://astro.build/config
export default defineConfig({
  integrations: [react()],
  server: { port: 4321 },
  vite: {
    server: {
      proxy: {
        '/api': { target: backendUrl, changeOrigin: true },
      },
    },
  },
});
