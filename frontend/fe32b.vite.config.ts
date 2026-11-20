// Temporary isolated dev config for task #32B live acceptance: proxies /api and
// /ws to a fresh backend on 8010 (current code) instead of the stale shared 8000.
import base from './vite.config'
import { defineConfig } from 'vite'

export default defineConfig({
  ...base,
  server: {
    ...base.server,
    port: 5179,
    strictPort: true,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8010', changeOrigin: true },
      '/ws': { target: 'http://127.0.0.1:8010', changeOrigin: true, ws: true },
    },
  },
})