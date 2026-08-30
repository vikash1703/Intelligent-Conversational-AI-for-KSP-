import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig(({ mode }) => ({
  plugins: [react()],
  // Catalyst Web Client Hosting serves deployed apps under /app/, not domain
  // root — asset URLs must be rooted there in production builds. Local dev
  // (vite/vite preview) stays at "/".
  base: mode === 'production' ? '/app/' : '/',
  // maplibre-gl (Hotspot Map's 3D view, added 2026-08-30) spins up its tile
  // worker via a `new Worker(new URL(...), { type: "module" })` call that
  // resolves relative to its own package location — Vite's dev-time
  // dependency pre-bundling rewrites that file into .vite/deps/, breaking the
  // worker's self-relative URL (live-verified: the worker request itself
  // 404s/ERR_FAILEDs in dev, and the map silently never finishes loading —
  // no thrown error, just nothing ever renders). Excluding it from
  // pre-bundling is maplibre-gl's own documented Vite workaround.
  optimizeDeps: { exclude: ['maplibre-gl'] },
}))
