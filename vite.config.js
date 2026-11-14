import { defineConfig } from 'vite';
import path from 'path';

export default defineConfig({
  root: 'web',
  publicDir: false,
  server: {
    port: process.env.PORT ? parseInt(process.env.PORT, 10) : 8600,
    host: '0.0.0.0',
    proxy: {
      '/api': {
        target: 'http://localhost:8600',
        changeOrigin: true
      }
    }
  },
  build: {
    outDir: '../dist',
    emptyOutDir: true
  }
});

