import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// The Go server (web/server) serves the built SPA and reverse-proxies /api and /auth
// to the Python Locket API. In dev we proxy the same paths to it so the SPA is same-origin.
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://localhost:8080', changeOrigin: true },
      '/auth': { target: 'http://localhost:8080', changeOrigin: true },
    },
  },
  build: { outDir: 'dist', emptyOutDir: true },
})
