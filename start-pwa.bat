@echo off
chcp 65001 >nul
title 启程 AI - APP 端（PWA 生产版）
set "ROOT=%~dp0"

echo ============================================================
echo   启程 AI APP 端 / PWA
echo ============================================================
echo.
echo Service Worker 只在生产构建里注册，所以这里先 build 再 preview。
echo 首次构建约 15 秒，构建完会自动起服务。
echo.
echo  本机：  http://127.0.0.1:4173   （Chrome 地址栏会出现「安装」图标）
echo  手机：  需 HTTPS 才能安装，见 docs/run-local.md
echo.
echo 注意：preview 提供的是构建产物，改代码不会自动生效，要重新跑本脚本。
echo ------------------------------------------------------------
echo.

start "启程AI-PWA" cmd /k "cd /d %ROOT%frontend && npm run pwa"

echo 已在新窗口执行 npm run pwa，等它输出 Local: http://localhost:4173 即可访问。
pause
