import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  preview: {
    // allow access through a Cloudflare quick tunnel
    allowedHosts: ['.trycloudflare.com'],
  },
  server: {
    allowedHosts: ['.trycloudflare.com'],
  },
})
