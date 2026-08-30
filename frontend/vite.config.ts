import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // duckdb-wasm is only reached through a dynamic import inside the Experiments
  // page, and its package.json `exports` map has no "." entry. Listing it here
  // makes Vite pre-bundle it at server start rather than relying on the dep
  // scanner finding it, so a dev server that was running when the package was
  // installed does not fail with "Failed to resolve import".
  optimizeDeps: {
    include: ['@duckdb/duckdb-wasm'],
  },
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
    sourcemap: false,
    minify: 'esbuild',
    rollupOptions: {
      output: {
        manualChunks: {
          vendor: ['react', 'react-dom'],
          mui: ['@mui/material', '@mui/x-charts', '@mui/x-data-grid'],
          charts: ['lightweight-charts', 'recharts'],
        },
      },
    },
  },
  server: {
    host: true,
    port: 5173,
    // src is a bind mount in docker-compose; macOS/Docker drops inotify events,
    // so edits can be served from a stale transform until the server restarts.
    watch: { usePolling: true, interval: 300 },
  },
  preview: {
    host: true,
    port: 4173,
  },
})
