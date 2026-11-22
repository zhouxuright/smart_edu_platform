import React from 'react'
import ReactDOM from 'react-dom/client'
import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import App from './App.jsx'
import { appTheme } from './theme.js'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <ConfigProvider theme={appTheme} locale={zhCN}>
    <App />
  </ConfigProvider>,
)

// 只在生产构建里注册 Service Worker：
// 开发环境频繁改文件，缓存会让人看到旧页面，排查起来非常费劲。
if (import.meta.env.PROD && 'serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {
      /* 注册失败不影响使用，静默即可 */
    })
  })
}
