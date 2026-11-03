import { resolve } from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Two pages: the novice's phone guide and the laptop dashboard. The build is committed under
// web/build so the server runs without Node; `npm run dev` proxies the API to a running server.
const SERVER = 'http://localhost:8000'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    outDir: '../build',
    emptyOutDir: true,
    rollupOptions: { input: { index: resolve(__dirname, 'index.html'), guide: resolve(__dirname, 'guide.html') } },
  },
  server: {
    proxy: {
      '/api': SERVER,
      '/procedures': SERVER,
      '/ws': { target: SERVER.replace('http', 'ws'), ws: true },
    },
  },
})
