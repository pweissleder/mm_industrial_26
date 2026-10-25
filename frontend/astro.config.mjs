// @ts-check
import { defineConfig } from 'astro/config';
import react from '@astrojs/react';
import basicSsl from '@vitejs/plugin-basic-ssl';

// Requests to /api/* from the dev server are forwarded to FastAPI, so the
// browser only ever talks to one origin and no CORS is needed locally.
const backendUrl = process.env.BACKEND_URL ?? 'http://localhost:8000';
// File events don't cross Docker bind mounts reliably on Windows/macOS.
const usePolling = process.env.VITE_USE_POLLING === 'true';
// Phone testing (opt-in, Start-AriNav.ps1 -Phone): phone browsers only give motion/orientation
// sensors to secure pages, so the dev server can serve HTTPS with a throw-away self-signed
// certificate (nothing is installed on the system; the phone shows a warning once) on the LAN.
const phone = process.env.ARINAV_PHONE === '1';

// https://astro.build/config
export default defineConfig({
  integrations: [react()],
  server: { port: 4321, host: phone ? true : undefined },
  vite: {
    plugins: phone ? [basicSsl({ name: 'arinav-dev' })] : [],
    server: {
      watch: usePolling ? { usePolling: true, interval: 300 } : undefined,
      proxy: {
        // ws: the live presence channel (/api/live) is a WebSocket.
        '/api': { target: backendUrl, changeOrigin: true, ws: true },
      },
    },
  },
});
