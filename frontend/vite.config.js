import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 后端统一走代理，前端代码里只写 /api，换域名不用改代码
const API_TARGET = 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    // 默认只监听本机：开发服务不该默认暴露到整个局域网。
    // 手机 / 小程序真机调试时用 `npm run dev:lan`（CLI 的 --host 会覆盖这里）。
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': API_TARGET,
      '/static': API_TARGET,
    },
  },
  preview: {
    // PWA 只有在生产构建里才注册 Service Worker，
    // 所以手机「添加到主屏幕」要跑 build + preview，而不是 dev。
    host: '127.0.0.1',
    port: 4173,
    proxy: {
      '/api': API_TARGET,
      '/static': API_TARGET,
    },
  },
})
