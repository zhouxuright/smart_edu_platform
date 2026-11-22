@echo off
chcp 65001 >nul
title 启程 AI - 一键启动（后端 + 前端）
set "ROOT=%~dp0"

echo ============================================================
echo   启程 AI 本地启动
echo ============================================================
echo.

echo [1/2] 后端 API    http://127.0.0.1:8000/docs
start "启程AI-后端" cmd /k "cd /d %ROOT%backend && python -m uvicorn edu_app.main:app --host 0.0.0.0 --port 8000"

echo [2/2] 前端 Web    http://127.0.0.1:5173
start "启程AI-前端" cmd /k "cd /d %ROOT%frontend && npm run dev:lan"

echo.
echo ------------------------------------------------------------
echo  本机访问     http://127.0.0.1:5173
echo  手机访问     http://本机IP:5173   （手机与电脑连同一 WiFi）
echo  接口文档     http://127.0.0.1:8000/docs
echo.
echo  查本机 IP：命令行执行 ipconfig，看「IPv4 地址」
echo  演示账号：demo@example.com / Demo1234!
echo.
echo  两个新开的窗口别关，关掉就等于停服务。
echo ------------------------------------------------------------
pause
