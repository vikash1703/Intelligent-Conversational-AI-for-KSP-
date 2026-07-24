import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig(({ mode }) => ({
  plugins: [react()],
  // Catalyst Web Client Hosting serves deployed apps under /app/, not domain
  // root — asset URLs must be rooted there in production builds. Local dev
  // (vite/vite preview) stays at "/".
  base: mode === 'production' ? '/app/' : '/',
}))
