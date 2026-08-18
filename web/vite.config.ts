import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// En développement, Vite sert l'interface et relaie `/api` vers uvicorn : une seule
// origine, donc aucun CORS à configurer. En production, c'est l'inverse — uvicorn sert
// le contenu de `dist/`. Dans les deux cas l'interface appelle `/api/...` en relatif et
// ignore où elle est servie.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: './tests/preparation.ts',
    include: ['tests/**/*.test.tsx'],
  },
})
