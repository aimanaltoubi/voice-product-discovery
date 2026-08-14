import react from '@vitejs/plugin-react'
import path from 'path'
import { defineConfig } from 'vite'

// The frontend talks to the FastAPI backend (default http://localhost:8000).
// In dev, /api and /media are proxied so no CORS setup is needed.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') }
  },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
      '/media': { target: 'http://localhost:8000', changeOrigin: true }
    }
  }
})
