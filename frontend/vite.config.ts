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
    proxy: {
      // DEV: рабочий backend поднят на 8030 без --reload (порты 8000 и 8020
      // заняты зомби-сокетами упавших/убитых --reload серверов, которые не
      // освобождаются из текущей сессии). См. docs/HANDOFF.md, топология dev.
      '/api': { target: 'http://127.0.0.1:8030', changeOrigin: true },
      '/ws': { target: 'http://127.0.0.1:8030', changeOrigin: true, ws: true },
    },
  },
})