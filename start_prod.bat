@echo off
echo Starting AI Agent Education Platform (Production Mode)...
echo.
echo Starting Backend Server...
start "Backend API" /min cmd /c "cd backend && uvicorn edu_app.main:app --host 0.0.0.0 --port 8000"
echo Backend started on http://localhost:8000
echo.
echo Serving Frontend...
echo In production, frontend/dist would be served by Nginx or S3.
echo For this demo, we will serve it using 'serve' or similar, or just point to dev server.
echo.
echo Deployment Complete!
pause
