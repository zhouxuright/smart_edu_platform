import os
import subprocess
import sys
import time

def run_command(command, cwd=None):
    print(f"Executing: {command}")
    result = subprocess.run(command, shell=True, cwd=cwd)
    if result.returncode != 0:
        print(f"Error executing command: {command}")
        sys.exit(1)

def main():
    print("==================================================")
    print("   AI Agent Smart Education Platform - Deployment")
    print("==================================================")
    
    root_dir = os.getcwd()
    frontend_dir = os.path.join(root_dir, "frontend")
    backend_dir = os.path.join(root_dir, "backend")
    
    # 1. Frontend Build
    print("\n[1/3] Building Frontend for Production...")
    if os.path.exists(frontend_dir):
        # Check if node_modules exists, install if not
        if not os.path.exists(os.path.join(frontend_dir, "node_modules")):
            print("Installing frontend dependencies...")
            run_command("npm install", cwd=frontend_dir)
            
        print("Running Vite Build...")
        run_command("npm run build", cwd=frontend_dir)
        print("Frontend build complete! Artifacts in frontend/dist/")
    else:
        print("Frontend directory not found!")
        sys.exit(1)

    # 2. Backend Setup
    print("\n[2/3] Preparing Backend Environment...")
    if os.path.exists(backend_dir):
        print("Verifying database connection and seeding initial data...")
        # Run seed script
        run_command("python scripts/seed_knowledge.py", cwd=backend_dir)
        print("Backend is ready.")
    else:
        print("Backend directory not found!")
        sys.exit(1)

    # 3. Production Launch Simulation
    print("\n[3/3] Simulating Production Launch...")
    print("Creating production startup script (start_prod.bat)...")
    
    startup_script = """@echo off
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
"""
    with open("start_prod.bat", "w") as f:
        f.write(startup_script)
        
    print("Production build finished successfully!")
    print("To start the system, run 'start_prod.bat'")

if __name__ == "__main__":
    main()
