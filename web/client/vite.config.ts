import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath, URL } from 'node:url'

/**
 * The build lands in `web/client/dist`, which is the directory the Python server
 * serves verbatim. Nothing here knows anything about a vault: the dev server
 * proxies `/api` and `/s` to the real server so the browser sees one origin and
 * the session cookie behaves in development exactly as it does in production.
 */
const API_ORIGIN = process.env.VITE_API_ORIGIN ?? 'http://127.0.0.1:8765'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5178,
    strictPort: true,
    // 127.0.0.1, not 0.0.0.0. The server binds loopback by default and the dev
    // server has no business being more exposed than the thing it proxies to.
    host: '127.0.0.1',
    proxy: {
      // Regex keys, anchored. A bare string key is a *prefix* match, so '/s'
      // swallowed every '/src/…' module request in dev and the page never
      // booted. Anchoring both is the whole fix.
      '^/api/': { target: API_ORIGIN, changeOrigin: false },
      '^/s/': { target: API_ORIGIN, changeOrigin: false },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: false,
    // The served page runs under a CSP with no `unsafe-inline`. Vite emits no
    // inline script for a production build, and this keeps it that way: an
    // inlined asset becomes a `data:` URL in CSS, never a script tag.
    assetsInlineLimit: 4096,
    target: 'es2022',
  },
})
