// Scratch-конфиг для браузерного смока run7: proxy на run-стек :8033 (новый код).
// Временный файл Root-проверки, удалить после приёмки. Основной vite.config.ts не тронут.
import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    port: 5176,
    strictPort: true,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8033', changeOrigin: true },
      '/ws': { target: 'http://127.0.0.1:8033', changeOrigin: true, ws: true },
    },
  },
})