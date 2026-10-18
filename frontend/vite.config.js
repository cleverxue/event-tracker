import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 构建产物由后端 StaticFiles 托管在根路径，base 用相对路径，
// 保证 /signals、/timeline 等深链刷新时资源正常加载。
export default defineConfig({
  plugins: [react()],
  base: './',
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      // 本地开发：前端 5173 → 后端 8000
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
