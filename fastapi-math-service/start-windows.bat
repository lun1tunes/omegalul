@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo ERROR: .venv is missing. Run setup-windows.bat first.
  exit /b 1
)
if not exist "math-service.env" (
  echo ERROR: math-service.env is missing.
  echo Run: copy math-service.env.example math-service.env
  exit /b 1
)

for /f "usebackq eol=# tokens=1,* delims==" %%A in ("math-service.env") do if not "%%A"=="" set "%%A=%%B"

if not defined MATH_SERVICE_HOST set "MATH_SERVICE_HOST=127.0.0.1"
if not defined MATH_SERVICE_PORT set "MATH_SERVICE_PORT=8100"

echo Starting Math Service at http://%MATH_SERVICE_HOST%:%MATH_SERVICE_PORT%
echo Keep this window open. Press Ctrl+C to stop.
".venv\Scripts\python.exe" -m uvicorn app.main:app --host "%MATH_SERVICE_HOST%" --port "%MATH_SERVICE_PORT%"
exit /b %errorlevel%
