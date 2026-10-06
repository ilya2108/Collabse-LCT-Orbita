import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';

/**
 * Дев-сервер проксирует /api на локальный backend: FastAPI слушает 8000,
 * префикс /api/v1 совпадает — rewrite не нужен. В контейнере статику раздаёт
 * nginx (frontend/nginx.conf), рантайм-конфиг приходит из /config.js (window.__ENV__).
 */
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.VITE_DEV_API_PROXY ?? 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    sourcemap: false,
    // Ручное разбиение vendor-чанков намеренно не используем: manualChunks
    // легко образует цикл между чанками с порчей порядка инициализации.
    // Тяжёлые экраны (граф на ECharts, позже раннер теста) подключаются через
    // dynamic import — Vite режет их в отдельные чанки автоматически.
    // Чанк графа (ECharts ≈ 1.09 МБ / 360 КБ gzip) грузится только на странице графа.
    chunkSizeWarningLimit: 1200,
  },
});
