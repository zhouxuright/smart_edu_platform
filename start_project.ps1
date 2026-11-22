# 启动脚本 (Windows PowerShell)

Write-Host "正在启动 AI Agent 教育平台..." -ForegroundColor Green

# 1. 启动后端服务
Write-Host "1. 启动后端服务 (Port: 8000)..." -ForegroundColor Cyan
# 使用 Start-Process 打开新窗口运行后端，以免阻塞当前窗口
Start-Process powershell -ArgumentList "-NoExit", "-Command", "& {cd backend; Write-Host 'Backend is running...'; uvicorn edu_app.main:app --reload --host 0.0.0.0 --port 8000}"

# 等待几秒确保后端开始初始化
Start-Sleep -Seconds 3

# 2. 启动前端网页服务
Write-Host "2. 启动前端网页服务 (Port: 5173)..." -ForegroundColor Cyan
# 使用 Start-Process 打开新窗口运行前端
Start-Process powershell -ArgumentList "-NoExit", "-Command", "& {cd frontend; Write-Host 'Frontend is running...'; npm run dev}"

Write-Host "=============================================" -ForegroundColor Green
Write-Host "服务已启动！"
Write-Host "后端地址: http://localhost:8000"
Write-Host "前端地址: http://localhost:5173"
Write-Host "API文档:  http://localhost:8000/docs"
Write-Host "=============================================" -ForegroundColor Green
Write-Host "注意：微信小程序需手动打开 '微信开发者工具' 并导入 'miniprogram' 目录。" -ForegroundColor Yellow
