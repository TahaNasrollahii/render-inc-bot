import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The Mini App is a single-page client app. It is served statically by Render
// from `dist/`; all dynamic work goes through the Python API service.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: 'dist',
    target: 'es2020',
  },
  define: {
    // In production, set VITE_API_BASE env var on Render Static Site
    // to point at the API service URL (e.g. https://corridor-api.onrender.com/api)
    'window.__API_BASE__': JSON.stringify(process.env.VITE_API_BASE || '/api'),
  },
})
