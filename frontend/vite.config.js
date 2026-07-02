import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

// Flask backend (waitress) runs on 5101. During `pnpm dev` we proxy all
// backend routes there so the SPA can call the existing API untouched.
const BACKEND = 'http://127.0.0.1:5101'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': { target: BACKEND, changeOrigin: true },
      '/uploads': { target: BACKEND, changeOrigin: true },
      '/sw.js': { target: BACKEND, changeOrigin: true },
    },
  },
  worker: {
    // jassub (subtítulos ASS del player) instancia un Worker; con code-splitting
    // Vite exige formato ES para los workers.
    format: 'es',
  },
  build: {
    // Flask serves this in production (see app.py serve_spa route).
    outDir: 'dist',
    emptyOutDir: true,
    assetsDir: 'assets',
  },
})
